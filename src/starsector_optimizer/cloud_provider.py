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
from typing import Any
from collections.abc import Sequence

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
_FLEET_TRANSIENT_ERROR_CODES = frozenset(
    {
        "InvalidGroup.NotFound",
        "InvalidSecurityGroupID.NotFound",
        "InvalidLaunchTemplateName.NotFoundException",
        "InvalidLaunchTemplateId.VersionNotFound",
    }
)

# Maintain-mode fleet knobs (spec 22 §AWSProvider provisioning + §Worker drain).
# `create_fleet(Type="maintain")` is async: it returns a FleetId immediately and
# launches instances in the background, so we poll `describe_fleet_instances`
# toward the requested per-region target. The poll *timeout* is the caller-
# supplied `provision_timeout_seconds` (the `CampaignConfig` knob), NOT a
# constant — only the poll *interval* is fixed here.
_FLEET_INSTANCE_POLL_INTERVAL_SECONDS = 15.0
# Fleet states that still hold (or can relaunch) billable capacity — the set
# `list_fleets_by_tag` scans for leaked-fleet discovery + teardown. `deleted`,
# `deleted-running`, `deleted-terminating`, and `failed` are excluded.
_FLEET_ACTIVE_STATES = ("submitted", "active", "modifying")
# CapacityRebalance replacement strategy for maintain fleets. Both "launch" and
# "launch-before-terminate" are proactive (they act on the at-risk signal before
# reclaim); the real distinction is termination behavior. "launch" launches a
# replacement WITHOUT terminating the flagged original (AWS-documented), so an
# at-risk instance and its replacement both bill until AWS actually reclaims the
# original — a stable over-provision above TargetCapacity. "launch-before-
# terminate" instead holds capacity AT target by terminating the original once
# the replacement is up. This value is left at "launch" (the AWS default) as a
# deferred tuning knob; the over-provision it causes is why maintain campaigns
# set `capacity_rebalancing: false` (plain maintain already refills genuinely-
# reclaimed spot, and the Redis janitor + matchup_id dedup cover in-flight
# interruptions).
_CAPACITY_REBALANCE_REPLACEMENT_STRATEGY = "launch"
# ExcessCapacityTerminationPolicy for the maintain drain's target-lower step:
# "no-termination" means the fleet neither relaunches toward the lowered target
# nor self-selects victims — the caller then terminates the chosen idle ids.
_EXCESS_CAPACITY_NO_TERMINATION = "no-termination"
# Real EC2 raises this for a purged FleetId (rather than returning an empty
# `Fleets` list), so `get_fleet_target` maps it to None = "fleet gone".
_FLEET_NOT_FOUND_ERROR_CODES = frozenset({"InvalidFleetId.NotFound", "InvalidFleetIds.NotFound"})

# Error codes that mean "the resource is already gone" — i.e. the delete
# operation's postcondition is satisfied. Treat these as success during
# teardown rather than retrying for the full deadline. Concurrent teardown
# paths (CampaignManager.teardown sweep + per-study `finally:
# terminate_fleet`) routinely race on the same SG; without this, the
# loser of the race burns the full _SG_DELETE_DEADLINE_SECONDS budget.
_SG_DELETE_IDEMPOTENT_ERROR_CODES = frozenset(
    {
        "InvalidGroup.NotFound",
        "InvalidSecurityGroupID.NotFound",
    }
)


class FleetTeardownError(RuntimeError):
    """Aggregate raised when one or more regions fail during a multi-region
    teardown (`terminate_fleet` / `terminate_all_tagged`).

    Teardown processes every region even when an earlier region raises (so a
    throttle in one region can't strand another region's self-respawning
    maintain fleet); the per-region failures are collected and surfaced as this
    single error at the end. The `.failures` attribute maps region → the
    exception that region raised."""

    def __init__(self, failures: dict[str, Exception]) -> None:
        self.failures = failures
        regions = ", ".join(sorted(failures))
        super().__init__(f"teardown failed in region(s): {regions}")


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
        self,
        *,
        fleet_name: str,
        project_tag: str,
        regions: Sequence[str],
        ami_ids_by_region: dict[str, str],
        instance_types: Sequence[str],
        ssh_key_name: str,
        spot_allocation_strategy: str,
        target_workers: int,
        user_data: str,
        root_volume_size_gb: int | None = None,
        fleet_type: str = "instant",
        capacity_rebalance: bool = False,
        provision_timeout_seconds: float = 600.0,
    ) -> list[str]:
        """Launch a spot fleet. Return instance IDs that were launched.

        Every provisioned resource carries `Project=<project_tag>` AND
        `Fleet=<fleet_name>`. LT/SG names use `f"{project_tag}__{fleet_name}"`
        for per-fleet isolation (no collision when multiple studies share a
        project tag).

        `fleet_type="instant"` (default) is synchronous — the response carries
        launched instance IDs and there is no persistent fleet resource.
        `fleet_type="maintain"` is asynchronous: it captures `response["FleetId"]`,
        tags the fleet resource (both `Project` and `Fleet`), and polls
        `describe_fleet_instances` toward the per-region target (bounded by
        `provision_timeout_seconds`), returning the full discovered set.
        `capacity_rebalance` is honored only under maintain (adds
        `SpotOptions.MaintenanceStrategies.CapacityRebalance`).
        """

    @abc.abstractmethod
    def terminate_fleet(self, *, fleet_name: str, project_tag: str) -> int:
        """Targeted teardown — reap resources tagged BOTH project_tag AND
        fleet_name. Idempotent. Returns the number of instances terminated.

        Under maintain, deletes the matching fleet resource(s) FIRST
        (`delete_fleets`, `TerminateInstances=True`) before the instance/LT/SG
        backstop passes, so the fleet cannot relaunch what the backstop kills."""

    @abc.abstractmethod
    def terminate_all_tagged(self, project_tag: str) -> int:
        """Campaign-wide sweep — reap everything tagged `Project=project_tag`
        regardless of fleet name. Crash-recovery backstop. Idempotent.
        Returns the number of instances terminated. Under maintain, deletes
        matching fleet resource(s) FIRST (Project-tag match)."""

    @abc.abstractmethod
    def list_fleets_by_tag(
        self, project_tag: str, fleet_name: str | None = None, *, region: str
    ) -> list[str]:
        """FleetIds of fleets in `_FLEET_ACTIVE_STATES` whose Tags match
        `Project=project_tag` (AND `Fleet=fleet_name` when given). `describe_fleets`
        has no server-side tag filter, so the match is client-side over
        `Fleets[].Tags`. The discovery primitive for maintain teardown, the
        leaked-fleet audit, and the maintain drain."""

    @abc.abstractmethod
    def delete_fleets(
        self, fleet_ids: Sequence[str], *, region: str, terminate_instances: bool = True
    ) -> int:
        """`delete-fleets` on an explicit FleetId list. `terminate_instances=True`
        kills the fleet's instances atomically. Empty ids → 0, no API call.
        Idempotent. Returns the number of fleet IDs submitted for deletion."""

    @abc.abstractmethod
    def modify_fleet_target(
        self, fleet_id: str, target: int, *, region: str, excess_policy: str
    ) -> None:
        """`modify-fleet` `TotalTargetCapacity=target`. Used by the maintain drain
        to lower a regional fleet's target (`excess_policy="no-termination"` so
        the fleet neither relaunches nor self-terminates; the caller then
        terminates the chosen idle ids)."""

    @abc.abstractmethod
    def get_fleet_target(self, fleet_id: str, *, region: str) -> int | None:
        """Current `TotalTargetCapacity` of a fleet, read from
        `describe_fleets(FleetIds=[fleet_id])`'s
        `Fleets[0].TargetCapacitySpecification.TotalTargetCapacity`. Returns
        `None` if the fleet is not found.

        Used by the maintain drain to clamp the lowered target against the
        fleet's CURRENT target (`min(current, live)`), so an over-fulfilled
        fleet (live > target from CapacityRebalance) never has its target
        RAISED by the drain's arithmetic — the target moves monotonically
        down in both the reclaim and over-provision regimes."""

    @abc.abstractmethod
    def terminate_instances(self, instance_ids: Sequence[str], *, region: str) -> int:
        """Terminate an explicit subset of instance IDs in one region.

        The ONLY subset-termination primitive — every other terminate path is
        tag-scoped whole-fleet/project. Used by the honest-eval
        `WorkerDrainTicker` (spec 22 §"Worker drain (honest-eval)") to reap
        provably-idle surplus workers. Empty `instance_ids` → return 0 with no
        API call. Idempotent: terminating an already-terminating id is an AWS
        no-op. Returns the number of instance IDs submitted for termination."""

    @abc.abstractmethod
    def list_active(self, project_tag: str) -> list[dict]:
        """RUNNING + PENDING instances tagged `Project=project_tag`. Does NOT
        include launch templates or security groups."""

    @abc.abstractmethod
    def get_spot_price(self, region: str, instance_type: str) -> float:
        """Most-recent observed spot price in USD/hour."""

    def describe_ami_tag(
        self,
        *,
        ami_id: str,
        region: str,
        tag_key: str,
    ) -> str:
        """Look up a tag value on an AMI. Default impl raises AttributeError
        so the preflight code can skip the check on providers that don't
        support tag introspection (Hetzner stub / fakes).
        """
        raise AttributeError(
            f"{type(self).__name__} does not implement describe_ami_tag; "
            f"preflight AMI tag check will be skipped."
        )


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
        self,
        *,
        fleet_name: str,
        project_tag: str,
        regions: Sequence[str],
        ami_ids_by_region: dict[str, str],
        instance_types: Sequence[str],
        ssh_key_name: str,
        spot_allocation_strategy: str,
        target_workers: int,
        user_data: str,
        root_volume_size_gb: int | None = None,
        fleet_type: str = "instant",
        capacity_rebalance: bool = False,
        provision_timeout_seconds: float = 600.0,
    ) -> list[str]:
        instance_ids: list[str] = []
        per_region_target = max(1, target_workers // max(1, len(regions)))
        resource_name = _resource_name(project_tag, fleet_name)
        for region in regions:
            ami_id = ami_ids_by_region.get(region)
            if not ami_id:
                logger.warning(
                    "provision_fleet: no AMI id for region=%s; skipping",
                    region,
                )
                continue
            sg_id = self._ensure_security_group(
                region=region,
                resource_name=resource_name,
                project_tag=project_tag,
                fleet_name=fleet_name,
            )
            self._ensure_launch_template(
                region=region,
                resource_name=resource_name,
                project_tag=project_tag,
                fleet_name=fleet_name,
                ami_id=ami_id,
                key_name=ssh_key_name,
                sg_id=sg_id,
                user_data=user_data,
                root_volume_size_gb=root_volume_size_gb,
            )
            instance_ids.extend(
                self._create_fleet_in_region(
                    region=region,
                    resource_name=resource_name,
                    project_tag=project_tag,
                    fleet_name=fleet_name,
                    instance_types=instance_types,
                    spot_allocation_strategy=spot_allocation_strategy,
                    target=per_region_target,
                    fleet_type=fleet_type,
                    capacity_rebalance=capacity_rebalance,
                    provision_timeout_seconds=provision_timeout_seconds,
                )
            )
        return instance_ids

    def _ensure_security_group(
        self,
        *,
        region: str,
        resource_name: str,
        project_tag: str,
        fleet_name: str,
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
            TagSpecifications=[
                {
                    "ResourceType": "security-group",
                    "Tags": [
                        {"Key": _PROJECT_KEY, "Value": project_tag},
                        {"Key": _FLEET_KEY, "Value": fleet_name},
                    ],
                }
            ],
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
        self,
        *,
        region: str,
        resource_name: str,
        project_tag: str,
        fleet_name: str,
        ami_id: str,
        key_name: str,
        sg_id: str,
        user_data: str,
        root_volume_size_gb: int | None = None,
    ) -> None:
        client = self._client(region)
        user_data_b64 = base64.b64encode(user_data.encode("utf-8")).decode("ascii")
        ebs: dict[str, Any] = {"DeleteOnTermination": True}
        if root_volume_size_gb is not None:
            ebs["VolumeSize"] = root_volume_size_gb
        launch_template_data = {
            "ImageId": ami_id,
            "KeyName": key_name,
            "SecurityGroupIds": [sg_id],
            "UserData": user_data_b64,
            "InstanceMarketOptions": {"MarketType": "spot"},
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": ebs,
                }
            ],
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
                TagSpecifications=[
                    {
                        "ResourceType": "launch-template",
                        "Tags": [
                            {"Key": _PROJECT_KEY, "Value": project_tag},
                            {"Key": _FLEET_KEY, "Value": fleet_name},
                        ],
                    }
                ],
            )

    def _create_fleet_in_region(
        self,
        *,
        region: str,
        resource_name: str,
        project_tag: str,
        fleet_name: str,
        instance_types: Sequence[str],
        spot_allocation_strategy: str,
        target: int,
        fleet_type: str = "instant",
        capacity_rebalance: bool = False,
        provision_timeout_seconds: float = 600.0,
    ) -> list[str]:
        client = self._client(region)
        launch_template_configs = [
            {
                "LaunchTemplateSpecification": {
                    "LaunchTemplateName": resource_name,
                    "Version": "$Latest",
                },
                "Overrides": [{"InstanceType": itype} for itype in instance_types],
            }
        ]
        # `Type="instant"` = ephemeral fleet; no fleet resource remains to
        # clean up. `MaintenanceStrategies` (CapacityRebalance) is only valid
        # on `Type="maintain"` fleets. Under instant, spot preemption is handled
        # by the Redis janitor + matchup_id dedup instead; under maintain the
        # fleet self-replenishes reclaimed spot toward TargetCapacity.
        spot_options: dict[str, Any] = {
            "AllocationStrategy": spot_allocation_strategy,
        }
        if fleet_type == "maintain" and capacity_rebalance:
            spot_options["MaintenanceStrategies"] = {
                "CapacityRebalance": {
                    "ReplacementStrategy": _CAPACITY_REBALANCE_REPLACEMENT_STRATEGY,
                }
            }
        both_tags = [
            {"Key": _PROJECT_KEY, "Value": project_tag},
            {"Key": _FLEET_KEY, "Value": fleet_name},
        ]
        # `create_fleet` tag scoping differs by fleet type:
        #  - instant: instances are created synchronously, so the call tags
        #    `ResourceType:"instance"` directly (belt-and-suspenders with the LT).
        #  - maintain: the call accepts ONLY `ResourceType:"fleet"` — AWS rejects
        #    an "instance" tag on a maintain CreateFleet (InvalidTagKey.Malformed),
        #    because instances are launched asynchronously by the fleet and inherit
        #    their tags from the launch template (which already tags instance +
        #    volume with both keys). Tagging the fleet resource lets
        #    `list_fleets_by_tag` rediscover it for teardown + the leaked-fleet
        #    audit purely from AWS state (no on-disk FleetId manifest).
        if fleet_type == "maintain":
            return self._create_maintain_fleet_in_region(
                client,
                region=region,
                spot_options=spot_options,
                launch_template_configs=launch_template_configs,
                tag_specifications=[{"ResourceType": "fleet", "Tags": both_tags}],
                target=target,
                provision_timeout_seconds=provision_timeout_seconds,
            )
        tag_specifications: list[dict[str, Any]] = [{"ResourceType": "instance", "Tags": both_tags}]
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
                TagSpecifications=tag_specifications,
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
                e.get("ErrorCode") in _FLEET_TRANSIENT_ERROR_CODES for e in last_errors
            )
            if not any_transient or attempt == _FLEET_PROVISION_MAX_RETRIES - 1:
                break
            logger.warning(
                "create_fleet in %s transient visibility errors "
                "(attempt %d/%d): %s; retrying after %.1fs",
                region,
                attempt + 1,
                _FLEET_PROVISION_MAX_RETRIES,
                sorted(
                    {str(e.get("ErrorCode")) for e in last_errors} & _FLEET_TRANSIENT_ERROR_CODES
                ),
                _FLEET_PROVISION_RETRY_DELAY_SECONDS,
            )
            time.sleep(_FLEET_PROVISION_RETRY_DELAY_SECONDS)
        if last_errors:
            if not ids:
                logger.error("create_fleet errors in %s: %s", region, last_errors)
                raise RuntimeError(
                    f"create_fleet produced zero instances in {region}; errors: {last_errors}"
                )
            logger.warning(
                "create_fleet partial errors in %s (fleet provisioned %d "
                "instance(s) despite these): %s",
                region,
                len(ids),
                last_errors,
            )
        return ids

    def _create_maintain_fleet_in_region(
        self,
        client: Any,
        *,
        region: str,
        spot_options: dict[str, Any],
        launch_template_configs: list[dict[str, Any]],
        tag_specifications: list[dict[str, Any]],
        target: int,
        provision_timeout_seconds: float,
    ) -> list[str]:
        """Provision a `Type="maintain"` fleet (async) and poll its instances.

        `create_fleet(Type="maintain")` returns a `FleetId` immediately and
        launches spot instances in the background, so we poll
        `describe_fleet_instances` toward `target` until it is reached OR
        `provision_timeout_seconds` elapses, then return the FULL discovered
        set (a genuine shortfall surfaces downstream via
        `min_workers_to_start`/`partial_fleet_policy`, NOT via an early
        return at >= 1). The transient SG/LT-visibility retry still guards
        the create call itself.
        """
        fleet_id = ""
        last_errors: list[dict] = []
        for attempt in range(_FLEET_PROVISION_MAX_RETRIES):
            response = client.create_fleet(
                SpotOptions=spot_options,
                TargetCapacitySpecification={
                    "TotalTargetCapacity": target,
                    "DefaultTargetCapacityType": "spot",
                },
                LaunchTemplateConfigs=launch_template_configs,
                Type="maintain",
                TagSpecifications=tag_specifications,
            )
            fleet_id = response.get("FleetId", "") or ""
            last_errors = response.get("Errors", [])
            if fleet_id:
                break
            any_transient = any(
                e.get("ErrorCode") in _FLEET_TRANSIENT_ERROR_CODES for e in last_errors
            )
            if not any_transient or attempt == _FLEET_PROVISION_MAX_RETRIES - 1:
                break
            logger.warning(
                "create_fleet (maintain) in %s transient visibility errors "
                "(attempt %d/%d): %s; retrying after %.1fs",
                region,
                attempt + 1,
                _FLEET_PROVISION_MAX_RETRIES,
                sorted(
                    {str(e.get("ErrorCode")) for e in last_errors} & _FLEET_TRANSIENT_ERROR_CODES
                ),
                _FLEET_PROVISION_RETRY_DELAY_SECONDS,
            )
            time.sleep(_FLEET_PROVISION_RETRY_DELAY_SECONDS)
        if not fleet_id:
            logger.error("create_fleet (maintain) errors in %s: %s", region, last_errors)
            raise RuntimeError(
                f"create_fleet (maintain) returned no FleetId in {region}; errors: {last_errors}"
            )
        if last_errors:
            # Fleet created but some AZs/pools were rejected (e.g. a per-AZ
            # InvalidFleetConfiguration). Surface it — matching the instant path's
            # partial-error WARN — so a below-diversity maintain fleet is visible;
            # the shortfall also reaches min_workers_to_start via the poll below.
            logger.warning(
                "create_fleet (maintain) partial errors in %s (FleetId=%s "
                "provisioned despite these): %s",
                region,
                fleet_id,
                last_errors,
            )
        # NOTE: provision_fleet iterates regions sequentially, so under maintain
        # the worst-case provision wall time is len(regions) * provision_timeout_seconds
        # (each region polls up to the full timeout), vs the instant path's much
        # shorter transient-retry budget. See spec 22 §AWSProvider provisioning.
        deadline = time.monotonic() + provision_timeout_seconds
        ids: list[str] = []
        while True:
            ids = self._describe_fleet_active_instance_ids(client, fleet_id)
            if len(ids) >= target:
                break
            if time.monotonic() >= deadline:
                logger.warning(
                    "maintain fleet %s in %s reached %d/%d instance(s) before "
                    "the %.0fs provision timeout; returning the partial set "
                    "(min_workers_to_start decides)",
                    fleet_id,
                    region,
                    len(ids),
                    target,
                    provision_timeout_seconds,
                )
                break
            time.sleep(_FLEET_INSTANCE_POLL_INTERVAL_SECONDS)
        logger.info(
            "maintain fleet %s in %s active with %d instance(s)",
            fleet_id,
            region,
            len(ids),
        )
        return ids

    def _describe_fleet_active_instance_ids(self, client: Any, fleet_id: str) -> list[str]:
        """All `ActiveInstances[].InstanceId` for a fleet, `NextToken`-paginated."""
        ids: list[str] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"FleetId": fleet_id}
            if next_token:
                kwargs["NextToken"] = next_token
            response = client.describe_fleet_instances(**kwargs)
            for active in response.get("ActiveInstances", []):
                iid = active.get("InstanceId")
                if iid:
                    ids.append(iid)
            next_token = response.get("NextToken")
            if not next_token:
                break
        return ids

    # ---- fleet lifecycle primitives -------------------------------------

    def list_fleets_by_tag(
        self, project_tag: str, fleet_name: str | None = None, *, region: str
    ) -> list[str]:
        """FleetIds of active-state fleets whose Tags match `project_tag`
        (AND `fleet_name` when given). `describe_fleets` has no server-side tag
        filter, so we filter by `fleet-state` server-side and match tags
        client-side over `Fleets[].Tags`. `NextToken`-paginated."""
        client = self._client(region)
        matched: list[str] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "Filters": [{"Name": "fleet-state", "Values": list(_FLEET_ACTIVE_STATES)}],
            }
            if next_token:
                kwargs["NextToken"] = next_token
            response = client.describe_fleets(**kwargs)
            for fleet in response.get("Fleets", []):
                tags = {t.get("Key"): t.get("Value") for t in (fleet.get("Tags") or [])}
                if tags.get(_PROJECT_KEY) != project_tag:
                    continue
                if fleet_name is not None and tags.get(_FLEET_KEY) != fleet_name:
                    continue
                fleet_id = fleet.get("FleetId")
                if fleet_id:
                    matched.append(fleet_id)
            next_token = response.get("NextToken")
            if not next_token:
                break
        return matched

    def delete_fleets(
        self, fleet_ids: Sequence[str], *, region: str, terminate_instances: bool = True
    ) -> int:
        """`delete-fleets` on an explicit FleetId list. Empty → 0, no API call."""
        ids = list(fleet_ids)
        if not ids:
            return 0
        client = self._client(region)
        client.delete_fleets(FleetIds=ids, TerminateInstances=terminate_instances)
        logger.info(
            "deleted %d fleet(s) in %s (terminate_instances=%s): %s",
            len(ids),
            region,
            terminate_instances,
            ids,
        )
        return len(ids)

    def modify_fleet_target(
        self, fleet_id: str, target: int, *, region: str, excess_policy: str
    ) -> None:
        """`modify-fleet` `TotalTargetCapacity=target` for the maintain drain."""
        client = self._client(region)
        client.modify_fleet(
            FleetId=fleet_id,
            TargetCapacitySpecification={"TotalTargetCapacity": target},
            ExcessCapacityTerminationPolicy=excess_policy,
        )
        logger.info(
            "modified fleet %s in %s to target=%d (excess_policy=%s)",
            fleet_id,
            region,
            target,
            excess_policy,
        )

    def get_fleet_target(self, fleet_id: str, *, region: str) -> int | None:
        """`TotalTargetCapacity` of `fleet_id`, or None if the fleet is gone.

        Real EC2 raises `InvalidFleetId.NotFound` for a purged fleet id (not an
        empty `Fleets` list), so that code maps to `None` = "fleet gone" — the
        drain then skips the target-lower and proceeds to terminate the idle ids
        (nothing will respawn them). A deleted-but-not-yet-purged fleet is
        returned with its (usually 0) target. Any OTHER error (e.g. a throttle)
        propagates, so the drain caller skips terminating rather than guessing.
        The empty-`Fleets` check is retained as belt-and-suspenders."""
        client = self._client(region)
        try:
            response = client.describe_fleets(FleetIds=[fleet_id])
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if code in _FLEET_NOT_FOUND_ERROR_CODES:
                return None
            raise
        fleets = response.get("Fleets", [])
        if not fleets:
            return None
        spec = fleets[0].get("TargetCapacitySpecification") or {}
        target = spec.get("TotalTargetCapacity")
        if target is None:
            # A live EC2 fleet always returns TotalTargetCapacity (a required
            # response field), so this branch is defensive only; treating an
            # absent target as "gone" (→ drain proceeds to terminate) is safe
            # because a fleet with no reported target isn't holding capacity.
            return None
        return int(target)

    # ---- teardown -------------------------------------------------------

    def terminate_fleet(self, *, fleet_name: str, project_tag: str) -> int:
        """Targeted reap: filter by BOTH tags.

        Under maintain, delete the matching persistent fleet resource(s) FIRST
        (both-tag match, so a sibling study's fleet survives) so the fleet
        cannot relaunch what the instance/LT/SG backstop terminates. Under
        instant, `list_fleets_by_tag` returns empty → the fleet step is a
        no-op and the path is unchanged. Idempotent.

        Each region's body is isolated: a `ClientError` (e.g. a throttle) in one
        region is collected, and processing continues to the remaining regions
        (so one region's failure can't strand another region's self-respawning
        maintain fleet), then a `FleetTeardownError` aggregate is raised at the
        end. Fleet-FIRST ordering is preserved within each region."""
        total = 0
        failures: dict[str, Exception] = {}
        for region in self._regions:
            try:
                fleet_ids = self.list_fleets_by_tag(project_tag, fleet_name, region=region)
                self.delete_fleets(fleet_ids, region=region, terminate_instances=True)
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
            except Exception as e:
                logger.exception("terminate_fleet: region %s failed; continuing", region)
                failures[region] = e
        if failures:
            raise FleetTeardownError(failures)
        return total

    def terminate_all_tagged(self, project_tag: str) -> int:
        """Sweep: filter by Project tag only. Crash-recovery backstop.

        Under maintain, delete every matching persistent fleet resource FIRST
        (Project-only match) so the sweep cannot race a relaunch. Under instant
        the fleet step is a no-op. Idempotent.

        Region-isolated like `terminate_fleet`: a failure in one region is
        collected and the sweep continues to the rest, then a
        `FleetTeardownError` aggregate is raised at the end. Fleet-FIRST
        ordering is preserved within each region."""
        total = 0
        failures: dict[str, Exception] = {}
        for region in self._regions:
            try:
                fleet_ids = self.list_fleets_by_tag(project_tag, region=region)
                self.delete_fleets(fleet_ids, region=region, terminate_instances=True)
                total += self._terminate_by_tags(region, {_PROJECT_KEY: project_tag})
                self._delete_launch_templates_by_tags(region, {_PROJECT_KEY: project_tag})
                self._delete_security_groups_by_tags(region, {_PROJECT_KEY: project_tag})
            except Exception as e:
                logger.exception("terminate_all_tagged: region %s failed; continuing", region)
                failures[region] = e
        if failures:
            raise FleetTeardownError(failures)
        return total

    def terminate_instances(self, instance_ids: Sequence[str], *, region: str) -> int:
        """Terminate an explicit subset of instance IDs in one region.

        Empty `instance_ids` short-circuits with no API call (the honest-eval
        drain calls this only when it has ids, but the guard keeps the
        primitive safe for any caller). Idempotent at the AWS layer."""
        ids = list(instance_ids)
        if not ids:
            return 0
        client = self._client(region)
        client.terminate_instances(InstanceIds=ids)
        logger.info("terminated %d instances in %s: %s", len(ids), region, ids)
        return len(ids)

    def _terminate_by_tags(self, region: str, tags: dict[str, str]) -> int:
        client = self._client(region)
        filters = [{"Name": f"tag:{k}", "Values": [v]} for k, v in tags.items()]
        filters.append(
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}
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
            len(ids),
            region,
            tags,
        )
        return len(ids)

    def _delete_launch_templates_by_tags(
        self,
        region: str,
        tags: dict[str, str],
    ) -> None:
        client = self._client(region)
        filters = [{"Name": f"tag:{k}", "Values": [v]} for k, v in tags.items()]
        try:
            existing = client.describe_launch_templates(Filters=filters).get(
                "LaunchTemplates",
                [],
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
                    "delete_launch_template(%s) failed in %s: %s",
                    name,
                    region,
                    e,
                )

    def _delete_security_groups_by_tags(
        self,
        region: str,
        tags: dict[str, str],
    ) -> None:
        """Delete SGs matching ALL tags, retrying past AWS's ENI-detach delay.

        After `terminate_instances` returns, AWS keeps ENIs attached to the
        SG for up to ~3 min while the network teardown runs. `delete_security_group`
        during this window fails with `DependencyViolation`. Poll-retry with
        a bounded deadline; a genuinely stuck SG (e.g. still used by an
        unexpected instance we missed) surfaces as a warning after the deadline.
        """
        client = self._client(region)
        filters = [{"Name": f"tag:{k}", "Values": [v]} for k, v in tags.items()]
        try:
            existing = client.describe_security_groups(Filters=filters).get(
                "SecurityGroups",
                [],
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
                        sg.get("GroupName", "?"),
                        group_id,
                        region,
                    )
                    break
                except Exception as e:
                    code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
                    if code in _SG_DELETE_IDEMPOTENT_ERROR_CODES:
                        logger.info(
                            "security group %s (id=%s) already gone in %s "
                            "(concurrent teardown won the race; treating as success)",
                            sg.get("GroupName", "?"),
                            group_id,
                            region,
                        )
                        break
                    last_error = e
                    time.sleep(_SG_DELETE_POLL_INTERVAL_SECONDS)
            else:
                logger.warning(
                    "delete_security_group(id=%s) gave up after %.0fs in %s: %s",
                    group_id,
                    _SG_DELETE_DEADLINE_SECONDS,
                    region,
                    last_error,
                )

    # ---- introspection --------------------------------------------------

    def list_active(self, project_tag: str) -> list[dict]:
        active: list[dict] = []
        for region in self._regions:
            active.extend(self._describe_active(region, project_tag))
        return active

    def _describe_active(self, region: str, project_tag: str) -> list[dict]:
        client = self._client(region)
        response = client.describe_instances(
            Filters=[
                {"Name": f"tag:{_PROJECT_KEY}", "Values": [project_tag]},
                {"Name": "instance-state-name", "Values": ["pending", "running"]},
            ]
        )
        out: list[dict] = []
        for reservation in response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                out.append(
                    {
                        "id": inst["InstanceId"],
                        "region": region,
                        "state": inst["State"]["Name"],
                        "instance_type": inst.get("InstanceType", "unknown"),
                    }
                )
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

    def describe_ami_tag(
        self,
        *,
        ami_id: str,
        region: str,
        tag_key: str,
    ) -> str:
        """Read a tag value off the named AMI (image-id must match exactly).

        Raises ValueError if the AMI doesn't exist or isn't tagged with
        `tag_key`. Preflight uses this to cross-check baked GameVersion
        against the committed manifest — mismatch blocks campaign launch.
        """
        client = self._client(region)
        response = client.describe_images(ImageIds=[ami_id])
        images = response.get("Images", [])
        if not images:
            raise ValueError(
                f"AMI {ami_id} not found in region {region}; "
                f"re-bake via scripts/cloud/packer or update ami_ids_by_region."
            )
        for tag in images[0].get("Tags") or []:
            if tag.get("Key") == tag_key:
                return str(tag.get("Value", ""))
        raise ValueError(
            f"AMI {ami_id} in {region} has no tag '{tag_key}'. "
            f"Re-bake with the tag set in scripts/cloud/packer/aws.pkr.hcl."
        )


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

    def list_fleets_by_tag(
        self, project_tag: str, fleet_name: str | None = None, *, region: str
    ) -> list[str]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def delete_fleets(
        self, fleet_ids: Sequence[str], *, region: str, terminate_instances: bool = True
    ) -> int:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def modify_fleet_target(
        self, fleet_id: str, target: int, *, region: str, excess_policy: str
    ) -> None:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def get_fleet_target(self, fleet_id: str, *, region: str) -> int | None:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def terminate_instances(self, instance_ids: Sequence[str], *, region: str) -> int:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def list_active(self, project_tag: str) -> list[dict]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def get_spot_price(self, region: str, instance_type: str) -> float:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)
