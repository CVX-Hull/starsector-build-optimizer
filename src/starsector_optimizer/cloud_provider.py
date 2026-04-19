"""Cloud provider abstraction — AWS-only MVP; Hetzner stubbed for future.

See docs/specs/22-cloud-deployment.md. Uses boto3 directly (no Libcloud);
the abstraction is a narrow ABC so a Libcloud wrapper can slot in later
without refactoring callers.

Two-tag scheme: every provisioned resource (instance / LT / SG) carries
BOTH `Project=<project_tag>` AND `Fleet=<fleet_name>`. `terminate_fleet`
targets a specific fleet via both tags; `terminate_all_tagged` sweeps via
the Project tag only (crash-recovery backstop).
"""

from __future__ import annotations

import abc
import base64
import logging
import time
from typing import Any, Sequence

logger = logging.getLogger(__name__)


_PROJECT_KEY = "Project"
_FLEET_KEY = "Fleet"

# SG deletion has to wait for ENI detachment from terminated instances; the
# AWS-observed upper bound is ~3 min. Polling every 10s keeps the loop tight
# without hammering the API.
_SG_DELETE_DEADLINE_SECONDS = 300.0
_SG_DELETE_POLL_INTERVAL_SECONDS = 10.0

# After `create_security_group`, the Fleet service can take several seconds to
# observe the new SG. A just-describe waiter (see `security_group_exists`) only
# confirms the SG appears in describe_security_groups; create_fleet's internal
# service replicates on a separate, slower path. Empirically a short extra retry
# loop on InvalidGroup.NotFound covers both the describe-visibility lag and the
# Fleet-service replication lag. See docs/specs/22-cloud-deployment.md § Fleet
# provisioning race.
_FLEET_PROVISION_MAX_RETRIES = 4
_FLEET_PROVISION_RETRY_DELAY_SECONDS = 3.0
_SG_VISIBILITY_WAITER_DELAY_SECONDS = 2
_SG_VISIBILITY_WAITER_MAX_ATTEMPTS = 10
# Error codes that indicate a just-created resource hasn't propagated to
# Fleet's internal service yet. All are transient (retry succeeds once the
# propagation completes); permanent errors like `InvalidFleetConfiguration`
# (AZ missing the requested instance type) are NOT in this set and short-
# circuit the retry loop.
_FLEET_TRANSIENT_ERROR_CODES = frozenset({
    "InvalidGroup.NotFound",
    "InvalidSecurityGroupID.NotFound",
    "InvalidLaunchTemplateName.NotFoundException",
    "InvalidLaunchTemplateId.VersionNotFound",
})

_HETZNER_STUB_MESSAGE = (
    "HetznerProvider is stubbed; implement when campaign budget >= $500. "
    "Hetzner's ~13% per-matchup advantage amortizes only at larger scale. "
    "See docs/reference/phase6-cloud-worker-federation.md section 3."
)


class CloudProvider(abc.ABC):
    """Abstract cloud provider. Implementations: AWSProvider (boto3 direct),
    HetznerProvider (stub until $500+ campaigns).

    The ABC is cloud-mechanical — no `CampaignConfig` dependency. Callers
    (study subprocess, probe script) compose the call from explicit fields.
    """

    @abc.abstractmethod
    def provision_fleet(
        self, *,
        fleet_name: str,
        project_tag: str,
        regions: Sequence[str],
        ami_ids_by_region: dict[str, str],
        instance_types: Sequence[str],
        ssh_key_name: str,
        spot_allocation_strategy: str,
        target_workers: int,
        user_data: str,
    ) -> list[str]:
        """Launch a spot fleet. Return instance IDs that were launched.

        Every provisioned resource carries `Project=<project_tag>` AND
        `Fleet=<fleet_name>`. LT/SG names use `f"{project_tag}__{fleet_name}"`
        for per-fleet isolation (no collision when multiple studies share a
        project tag).
        """

    @abc.abstractmethod
    def terminate_fleet(self, *, fleet_name: str, project_tag: str) -> int:
        """Targeted teardown — reap resources tagged BOTH project_tag AND
        fleet_name. Idempotent. Returns the number of instances terminated."""

    @abc.abstractmethod
    def terminate_all_tagged(self, project_tag: str) -> int:
        """Campaign-wide sweep — reap everything tagged `Project=project_tag`
        regardless of fleet name. Crash-recovery backstop. Idempotent.
        Returns the number of instances terminated."""

    @abc.abstractmethod
    def list_active(self, project_tag: str) -> list[dict]:
        """RUNNING + PENDING instances tagged `Project=project_tag`. Does NOT
        include launch templates or security groups."""

    @abc.abstractmethod
    def get_spot_price(self, region: str, instance_type: str) -> float:
        """Most-recent observed spot price in USD/hour."""


# ---- AWS ---------------------------------------------------------------------


def _resource_name(project_tag: str, fleet_name: str) -> str:
    """LT/SG name. Unique per (campaign, fleet) so two studies in the same
    region never collide. Matches AWS LT naming rules given a
    `load_campaign_config`-validated campaign name."""
    return f"{project_tag}__{fleet_name}"


class AWSProvider(CloudProvider):
    """AWS EC2 spot provider using boto3 directly.

    `provision_fleet` per region: ensure SG (outbound-only, no ingress) →
    ensure LT (bound to the SG, with base64 UserData + both tags) → fire
    an instant-type EC2 Fleet diversified across `instance_types`.

    Credentials come from the standard AWS credential chain.
    """

    def __init__(self, regions: Sequence[str]) -> None:
        self._regions = tuple(regions)
        self._clients: dict[str, Any] = {}

    def _client(self, region: str):
        if region not in self._clients:
            import boto3
            self._clients[region] = boto3.client("ec2", region_name=region)
        return self._clients[region]

    # ---- provision ------------------------------------------------------

    def provision_fleet(
        self, *,
        fleet_name: str,
        project_tag: str,
        regions: Sequence[str],
        ami_ids_by_region: dict[str, str],
        instance_types: Sequence[str],
        ssh_key_name: str,
        spot_allocation_strategy: str,
        target_workers: int,
        user_data: str,
    ) -> list[str]:
        instance_ids: list[str] = []
        per_region_target = max(1, target_workers // max(1, len(regions)))
        resource_name = _resource_name(project_tag, fleet_name)
        for region in regions:
            ami_id = ami_ids_by_region.get(region)
            if not ami_id:
                logger.warning(
                    "provision_fleet: no AMI id for region=%s; skipping", region,
                )
                continue
            sg_id = self._ensure_security_group(
                region=region, resource_name=resource_name,
                project_tag=project_tag, fleet_name=fleet_name,
            )
            self._ensure_launch_template(
                region=region, resource_name=resource_name,
                project_tag=project_tag, fleet_name=fleet_name,
                ami_id=ami_id, key_name=ssh_key_name,
                sg_id=sg_id, user_data=user_data,
            )
            instance_ids.extend(self._create_fleet_in_region(
                region=region, resource_name=resource_name,
                project_tag=project_tag, fleet_name=fleet_name,
                instance_types=instance_types,
                spot_allocation_strategy=spot_allocation_strategy,
                target=per_region_target,
            ))
        return instance_ids

    def _ensure_security_group(
        self, *,
        region: str, resource_name: str,
        project_tag: str, fleet_name: str,
    ) -> str:
        client = self._client(region)
        existing = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [resource_name]}],
        ).get("SecurityGroups", [])
        if existing:
            return existing[0]["GroupId"]
        response = client.create_security_group(
            GroupName=resource_name,
            Description=f"Starsector Phase 6 worker SG for {resource_name} (egress-only)",
            TagSpecifications=[{
                "ResourceType": "security-group",
                "Tags": [
                    {"Key": _PROJECT_KEY, "Value": project_tag},
                    {"Key": _FLEET_KEY, "Value": fleet_name},
                ],
            }],
        )
        sg_id = response["GroupId"]
        # Block until describe_security_groups returns the new SG. Under
        # concurrent provisioning (N studies creating SGs in parallel), the
        # create-then-fleet sequence without this waiter trips InvalidGroup.
        # NotFound in create_fleet. Fleet's own internal service replication
        # is handled by _create_fleet_in_region's retry loop.
        client.get_waiter("security_group_exists").wait(
            GroupIds=[sg_id],
            WaiterConfig={
                "Delay": _SG_VISIBILITY_WAITER_DELAY_SECONDS,
                "MaxAttempts": _SG_VISIBILITY_WAITER_MAX_ATTEMPTS,
            },
        )
        return sg_id

    def _ensure_launch_template(
        self, *,
        region: str,
        resource_name: str,
        project_tag: str,
        fleet_name: str,
        ami_id: str,
        key_name: str,
        sg_id: str,
        user_data: str,
    ) -> None:
        client = self._client(region)
        user_data_b64 = base64.b64encode(user_data.encode("utf-8")).decode("ascii")
        launch_template_data = {
            "ImageId": ami_id,
            "KeyName": key_name,
            "SecurityGroupIds": [sg_id],
            "UserData": user_data_b64,
            "InstanceMarketOptions": {"MarketType": "spot"},
            "BlockDeviceMappings": [{
                "DeviceName": "/dev/sda1",
                "Ebs": {"DeleteOnTermination": True},
            }],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": _PROJECT_KEY, "Value": project_tag},
                        {"Key": _FLEET_KEY, "Value": fleet_name},
                    ],
                },
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": _PROJECT_KEY, "Value": project_tag},
                        {"Key": _FLEET_KEY, "Value": fleet_name},
                    ],
                },
            ],
        }
        existing = client.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [resource_name]}],
        ).get("LaunchTemplates", [])
        if existing:
            response = client.create_launch_template_version(
                LaunchTemplateName=resource_name,
                LaunchTemplateData=launch_template_data,
            )
            new_version = response["LaunchTemplateVersion"]["VersionNumber"]
            client.modify_launch_template(
                LaunchTemplateName=resource_name,
                DefaultVersion=str(new_version),
            )
        else:
            client.create_launch_template(
                LaunchTemplateName=resource_name,
                LaunchTemplateData=launch_template_data,
                TagSpecifications=[{
                    "ResourceType": "launch-template",
                    "Tags": [
                        {"Key": _PROJECT_KEY, "Value": project_tag},
                        {"Key": _FLEET_KEY, "Value": fleet_name},
                    ],
                }],
            )

    def _create_fleet_in_region(
        self, *,
        region: str,
        resource_name: str,
        project_tag: str,
        fleet_name: str,
        instance_types: Sequence[str],
        spot_allocation_strategy: str,
        target: int,
    ) -> list[str]:
        client = self._client(region)
        launch_template_configs = [{
            "LaunchTemplateSpecification": {
                "LaunchTemplateName": resource_name,
                "Version": "$Latest",
            },
            "Overrides": [
                {"InstanceType": itype} for itype in instance_types
            ],
        }]
        # `Type="instant"` = ephemeral fleet; no fleet resource remains to
        # clean up. `MaintenanceStrategies` (CapacityRebalance) is only valid
        # on `Type="maintain"` fleets. Spot preemption is handled by the
        # Redis janitor + matchup_id dedup instead.
        spot_options: dict[str, Any] = {
            "AllocationStrategy": spot_allocation_strategy,
        }
        # Retry on transient "just-created resource not yet visible to Fleet"
        # errors. Fleet has a replication lag beyond what individual boto3
        # describe-waiters cover, and a few-second retry loop is sufficient.
        # Covers both security groups (`InvalidGroup.NotFound`) and launch
        # templates (`InvalidLaunchTemplateName.NotFoundException`, version
        # variant) under concurrent `provision_fleet` pressure.
        last_errors: list[dict] = []
        ids: list[str] = []
        for attempt in range(_FLEET_PROVISION_MAX_RETRIES):
            response = client.create_fleet(
                SpotOptions=spot_options,
                TargetCapacitySpecification={
                    "TotalTargetCapacity": target,
                    "DefaultTargetCapacityType": "spot",
                },
                LaunchTemplateConfigs=launch_template_configs,
                Type="instant",
                TagSpecifications=[{
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": _PROJECT_KEY, "Value": project_tag},
                        {"Key": _FLEET_KEY, "Value": fleet_name},
                    ],
                }],
            )
            last_errors = response.get("Errors", [])
            ids = []
            for instance in response.get("Instances", []):
                ids.extend(instance.get("InstanceIds", []))
            if ids:
                break
            # Retry if ANY error is a transient "just-created resource not yet
            # visible" error. Mixed permanent errors (e.g. `us-east-1e`
            # rejecting c7a.2xlarge with `InvalidFleetConfiguration`) co-occur
            # with transient SG/LT errors on the other AZs — retrying still
            # succeeds on the non-1e AZs once the resource propagates.
            any_transient = any(
                e.get("ErrorCode") in _FLEET_TRANSIENT_ERROR_CODES
                for e in last_errors
            )
            if not any_transient or attempt == _FLEET_PROVISION_MAX_RETRIES - 1:
                break
            logger.warning(
                "create_fleet in %s transient visibility errors "
                "(attempt %d/%d): %s; retrying after %.1fs",
                region, attempt + 1, _FLEET_PROVISION_MAX_RETRIES,
                sorted({e.get("ErrorCode") for e in last_errors}
                       & _FLEET_TRANSIENT_ERROR_CODES),
                _FLEET_PROVISION_RETRY_DELAY_SECONDS,
            )
            time.sleep(_FLEET_PROVISION_RETRY_DELAY_SECONDS)
        if last_errors:
            if not ids:
                logger.error("create_fleet errors in %s: %s", region, last_errors)
                raise RuntimeError(
                    f"create_fleet produced zero instances in {region}; "
                    f"errors: {last_errors}"
                )
            logger.warning(
                "create_fleet partial errors in %s (fleet provisioned %d "
                "instance(s) despite these): %s", region, len(ids), last_errors,
            )
        return ids

    # ---- teardown -------------------------------------------------------

    def terminate_fleet(self, *, fleet_name: str, project_tag: str) -> int:
        """Targeted reap: filter by BOTH tags."""
        total = 0
        for region in self._regions:
            total += self._terminate_by_tags(
                region,
                {_PROJECT_KEY: project_tag, _FLEET_KEY: fleet_name},
            )
            self._delete_launch_templates_by_tags(
                region,
                {_PROJECT_KEY: project_tag, _FLEET_KEY: fleet_name},
            )
            self._delete_security_groups_by_tags(
                region,
                {_PROJECT_KEY: project_tag, _FLEET_KEY: fleet_name},
            )
        return total

    def terminate_all_tagged(self, project_tag: str) -> int:
        """Sweep: filter by Project tag only. Crash-recovery backstop."""
        total = 0
        for region in self._regions:
            total += self._terminate_by_tags(region, {_PROJECT_KEY: project_tag})
            self._delete_launch_templates_by_tags(region, {_PROJECT_KEY: project_tag})
            self._delete_security_groups_by_tags(region, {_PROJECT_KEY: project_tag})
        return total

    def _terminate_by_tags(self, region: str, tags: dict[str, str]) -> int:
        client = self._client(region)
        filters = [
            {"Name": f"tag:{k}", "Values": [v]} for k, v in tags.items()
        ]
        filters.append(
            {"Name": "instance-state-name", "Values": ["pending", "running"]}
        )
        response = client.describe_instances(Filters=filters)
        ids: list[str] = []
        for reservation in response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                ids.append(inst["InstanceId"])
        if not ids:
            return 0
        client.terminate_instances(InstanceIds=ids)
        logger.info(
            "terminated %d instances in %s for tags=%s",
            len(ids), region, tags,
        )
        return len(ids)

    def _delete_launch_templates_by_tags(
        self, region: str, tags: dict[str, str],
    ) -> None:
        client = self._client(region)
        filters = [
            {"Name": f"tag:{k}", "Values": [v]} for k, v in tags.items()
        ]
        try:
            existing = client.describe_launch_templates(Filters=filters).get(
                "LaunchTemplates", [],
            )
        except Exception as e:
            logger.warning("describe_launch_templates failed in %s: %s", region, e)
            return
        for lt in existing:
            name = lt["LaunchTemplateName"]
            try:
                client.delete_launch_template(LaunchTemplateName=name)
                logger.info("deleted launch template %s in %s", name, region)
            except Exception as e:
                logger.warning(
                    "delete_launch_template(%s) failed in %s: %s", name, region, e,
                )

    def _delete_security_groups_by_tags(
        self, region: str, tags: dict[str, str],
    ) -> None:
        """Delete SGs matching ALL tags, retrying past AWS's ENI-detach delay.

        After `terminate_instances` returns, AWS keeps ENIs attached to the
        SG for up to ~3 min while the network teardown runs. `delete_security_group`
        during this window fails with `DependencyViolation`. Poll-retry with
        a bounded deadline; a genuinely stuck SG (e.g. still used by an
        unexpected instance we missed) surfaces as a warning after the deadline.
        """
        client = self._client(region)
        filters = [
            {"Name": f"tag:{k}", "Values": [v]} for k, v in tags.items()
        ]
        try:
            existing = client.describe_security_groups(Filters=filters).get(
                "SecurityGroups", [],
            )
        except Exception as e:
            logger.warning("describe_security_groups failed in %s: %s", region, e)
            return
        for sg in existing:
            group_id = sg["GroupId"]
            deadline = time.monotonic() + _SG_DELETE_DEADLINE_SECONDS
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    client.delete_security_group(GroupId=group_id)
                    logger.info(
                        "deleted security group %s (id=%s) in %s",
                        sg.get("GroupName", "?"), group_id, region,
                    )
                    break
                except Exception as e:
                    last_error = e
                    time.sleep(_SG_DELETE_POLL_INTERVAL_SECONDS)
            else:
                logger.warning(
                    "delete_security_group(id=%s) gave up after %.0fs in %s: %s",
                    group_id, _SG_DELETE_DEADLINE_SECONDS, region, last_error,
                )

    # ---- introspection --------------------------------------------------

    def list_active(self, project_tag: str) -> list[dict]:
        active: list[dict] = []
        for region in self._regions:
            active.extend(self._describe_active(region, project_tag))
        return active

    def _describe_active(self, region: str, project_tag: str) -> list[dict]:
        client = self._client(region)
        response = client.describe_instances(Filters=[
            {"Name": f"tag:{_PROJECT_KEY}", "Values": [project_tag]},
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ])
        out: list[dict] = []
        for reservation in response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                out.append({
                    "id": inst["InstanceId"],
                    "region": region,
                    "state": inst["State"]["Name"],
                    "instance_type": inst.get("InstanceType", "unknown"),
                })
        return out

    def get_spot_price(self, region: str, instance_type: str) -> float:
        client = self._client(region)
        response = client.describe_spot_price_history(
            InstanceTypes=[instance_type],
            MaxResults=1,
            ProductDescriptions=["Linux/UNIX"],
        )
        prices = response.get("SpotPriceHistory", [])
        if not prices:
            return 0.0
        return float(prices[0]["SpotPrice"])


# ---- Hetzner (stub until $500+ campaigns) -----------------------------------


class HetznerProvider(CloudProvider):
    """Stub until campaigns reach $500+ and the Hetzner ~13% per-matchup
    advantage amortizes. Every method raises NotImplementedError.
    """

    def provision_fleet(self, **kwargs) -> list[str]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def terminate_fleet(self, *, fleet_name: str, project_tag: str) -> int:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def terminate_all_tagged(self, project_tag: str) -> int:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def list_active(self, project_tag: str) -> list[dict]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def get_spot_price(self, region: str, instance_type: str) -> float:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)
