"""Cloud provider abstraction — AWS-only MVP; Hetzner stubbed for future.

See docs/specs/22-cloud-deployment.md. Uses boto3 directly (no Libcloud);
the abstraction is a narrow ABC so a Libcloud wrapper can slot in later
without refactoring callers.
"""

from __future__ import annotations

import abc
import base64
import logging
from typing import TYPE_CHECKING, Any, Sequence

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from .models import CampaignConfig


_TAG_KEY = "Project"
_TAG_PREFIX = "starsector"
_HETZNER_STUB_MESSAGE = (
    "HetznerProvider is stubbed; implement when campaign budget >= $500. "
    "Hetzner's ~13% per-matchup advantage amortizes only at larger scale. "
    "See docs/reference/phase6-cloud-worker-federation.md section 3."
)


class CloudProvider(abc.ABC):
    """Abstract cloud provider. Implementations: AWSProvider (boto3 direct),
    HetznerProvider (stub until $500+ campaigns)."""

    @abc.abstractmethod
    def create_fleet(
        self, config: "CampaignConfig", *, user_data: str,
    ) -> list[str]:
        """Launch a diversified spot fleet. Return instance IDs that ARE UP.

        user_data is a cloud-init script. The caller (probe script or
        CampaignManager) renders it via cloud_userdata.render_user_data or
        cloud_userdata.render_probe_user_data — the provider just base64-
        encodes and plumbs it into the LaunchTemplate.
        """

    @abc.abstractmethod
    def terminate_all_tagged(self, campaign_name: str) -> int:
        """Terminate every instance tagged Project=starsector-<campaign_name>,
        then delete the launch template and security group. Idempotent.
        Returns the number of instances terminated."""

    @abc.abstractmethod
    def list_active(self, campaign_name: str) -> list[dict]:
        """Return RUNNING + PENDING instances tagged for this campaign.
        Does NOT include launch templates or security groups — those are
        stateless scaffolding."""

    @abc.abstractmethod
    def get_spot_price(self, region: str, instance_type: str) -> float:
        """Most-recent observed spot price in USD/hour."""


# ---- AWS ---------------------------------------------------------------------


def _project_tag(campaign_name: str) -> str:
    return f"{_TAG_PREFIX}-{campaign_name}"


class AWSProvider(CloudProvider):
    """AWS EC2 spot provider using boto3 directly.

    create_fleet ensures a campaign-scoped LaunchTemplate + SecurityGroup
    per region, then fires EC2 Fleet diversified across config.instance_types
    with price-capacity-optimized + CapacityRebalancing. All resources
    share the tag Project=starsector-<campaign_name>, which
    terminate_all_tagged uses to reap them together.

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

    # ---- create_fleet --------------------------------------------------------

    def create_fleet(
        self, config: "CampaignConfig", *, user_data: str,
    ) -> list[str]:
        """Per region: ensure SG + LT, then fire an instant-type Fleet."""
        instance_ids: list[str] = []
        per_region_target = max(
            1, config.max_concurrent_workers // max(1, len(self._regions))
        )
        tag = _project_tag(config.name)
        for region in self._regions:
            ami_id = config.ami_ids_by_region.get(region)
            if not ami_id:
                logger.warning("create_fleet: no AMI id for region=%s; skipping", region)
                continue
            sg_id = self._ensure_security_group(region, tag)
            self._ensure_launch_template(
                region=region, tag=tag, ami_id=ami_id,
                key_name=config.ssh_key_name, sg_id=sg_id,
                user_data=user_data,
            )
            instance_ids.extend(self._create_fleet_in_region(
                region=region, tag=tag, config=config,
                target=per_region_target,
            ))
        return instance_ids

    def _ensure_security_group(self, region: str, tag: str) -> str:
        """Create-or-find the egress-only SG. Returns the group id."""
        client = self._client(region)
        existing = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [tag]}],
        ).get("SecurityGroups", [])
        if existing:
            return existing[0]["GroupId"]
        response = client.create_security_group(
            GroupName=tag,
            Description=f"Starsector Phase 6 worker SG for {tag} (egress-only)",
            TagSpecifications=[{
                "ResourceType": "security-group",
                "Tags": [{"Key": _TAG_KEY, "Value": tag}],
            }],
        )
        sg_id = response["GroupId"]
        # Default AWS SG has no ingress (correct) and allow-all egress
        # (correct — workers need Tailscale + apt + boto outbound). No mods.
        return sg_id

    def _ensure_launch_template(
        self, *,
        region: str,
        tag: str,
        ami_id: str,
        key_name: str,
        sg_id: str,
        user_data: str,
    ) -> None:
        """Create the LT if missing, otherwise append a new version.

        LaunchTemplate versions are immutable once referenced by a fleet; we
        never edit in place. Subsequent campaigns reuse the template name
        but get a fresh default version.
        """
        client = self._client(region)
        user_data_b64 = base64.b64encode(user_data.encode("utf-8")).decode("ascii")
        launch_template_data = {
            "ImageId": ami_id,
            "KeyName": key_name,
            "SecurityGroupIds": [sg_id],
            "UserData": user_data_b64,
            "InstanceMarketOptions": {"MarketType": "spot"},
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": _TAG_KEY, "Value": tag}],
                },
                {
                    "ResourceType": "volume",
                    "Tags": [{"Key": _TAG_KEY, "Value": tag}],
                },
            ],
        }
        existing = client.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [tag]}],
        ).get("LaunchTemplates", [])
        if existing:
            response = client.create_launch_template_version(
                LaunchTemplateName=tag,
                LaunchTemplateData=launch_template_data,
            )
            new_version = response["LaunchTemplateVersion"]["VersionNumber"]
            client.modify_launch_template(
                LaunchTemplateName=tag,
                DefaultVersion=str(new_version),
            )
        else:
            client.create_launch_template(
                LaunchTemplateName=tag,
                LaunchTemplateData=launch_template_data,
                TagSpecifications=[{
                    "ResourceType": "launch-template",
                    "Tags": [{"Key": _TAG_KEY, "Value": tag}],
                }],
            )

    def _create_fleet_in_region(
        self, *,
        region: str,
        tag: str,
        config: "CampaignConfig",
        target: int,
    ) -> list[str]:
        client = self._client(region)
        launch_template_configs = [{
            "LaunchTemplateSpecification": {
                "LaunchTemplateName": tag,
                "Version": "$Latest",
            },
            "Overrides": [
                {"InstanceType": itype}
                for itype in config.instance_types
            ],
        }]
        response = client.create_fleet(
            SpotOptions={
                "AllocationStrategy": config.spot_allocation_strategy,
                "MaintenanceStrategies": {
                    "CapacityRebalance": {
                        "ReplacementStrategy": "launch"
                        if config.capacity_rebalancing else "no-op",
                    },
                },
            },
            TargetCapacitySpecification={
                "TotalTargetCapacity": target,
                "DefaultTargetCapacityType": "spot",
            },
            LaunchTemplateConfigs=launch_template_configs,
            Type="instant",
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [{"Key": _TAG_KEY, "Value": tag}],
            }],
        )
        # Surface fleet-level errors explicitly — without this, an unavailable
        # AMI (still copying async) or missing IAM perm returns zero instances
        # with no diagnostic, and the probe reports a misleading "create_fleet
        # returned zero instance IDs". (Audit A finding, 2026-04-18.)
        errors = response.get("Errors", [])
        if errors:
            logger.error("create_fleet errors in %s: %s", region, errors)
        ids: list[str] = []
        for instance in response.get("Instances", []):
            ids.extend(instance.get("InstanceIds", []))
        if not ids and errors:
            raise RuntimeError(
                f"create_fleet produced zero instances in {region}; "
                f"errors: {errors}"
            )
        return ids

    # ---- teardown ------------------------------------------------------------

    def terminate_all_tagged(self, campaign_name: str) -> int:
        """Idempotent. Per region: terminate instances, delete LT, delete SG."""
        tag = _project_tag(campaign_name)
        total = 0
        for region in self._regions:
            total += self._terminate_instances(region, tag)
            self._delete_launch_template(region, tag)
            self._delete_security_group(region, tag)
        return total

    def _terminate_instances(self, region: str, tag: str) -> int:
        client = self._client(region)
        ids = [inst["id"] for inst in self._describe_active(region, tag)]
        if not ids:
            return 0
        client.terminate_instances(InstanceIds=ids)
        logger.info("terminated %d instances in %s for tag=%s",
                    len(ids), region, tag)
        return len(ids)

    def _delete_launch_template(self, region: str, tag: str) -> None:
        client = self._client(region)
        try:
            existing = client.describe_launch_templates(
                Filters=[{"Name": "launch-template-name", "Values": [tag]}],
            ).get("LaunchTemplates", [])
        except Exception as e:
            logger.warning("describe_launch_templates failed in %s: %s", region, e)
            return
        if not existing:
            return
        try:
            client.delete_launch_template(LaunchTemplateName=tag)
            logger.info("deleted launch template %s in %s", tag, region)
        except Exception as e:
            logger.warning("delete_launch_template failed in %s: %s", region, e)

    def _delete_security_group(self, region: str, tag: str) -> None:
        client = self._client(region)
        try:
            existing = client.describe_security_groups(
                Filters=[{"Name": "group-name", "Values": [tag]}],
            ).get("SecurityGroups", [])
        except Exception as e:
            logger.warning("describe_security_groups failed in %s: %s", region, e)
            return
        if not existing:
            return
        try:
            client.delete_security_group(GroupId=existing[0]["GroupId"])
            logger.info("deleted security group %s in %s", tag, region)
        except Exception as e:
            # SG deletion can race with in-flight instance tear-downs (AWS
            # holds the SG until all instances fully terminate). Log + move
            # on; final_audit.sh will flag any persistent leak.
            logger.warning("delete_security_group failed in %s: %s", region, e)

    # ---- introspection -------------------------------------------------------

    def list_active(self, campaign_name: str) -> list[dict]:
        tag = _project_tag(campaign_name)
        active: list[dict] = []
        for region in self._regions:
            active.extend(self._describe_active(region, tag))
        return active

    def _describe_active(self, region: str, tag: str) -> list[dict]:
        client = self._client(region)
        response = client.describe_instances(Filters=[
            {"Name": f"tag:{_TAG_KEY}", "Values": [tag]},
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

    def create_fleet(self, config: "CampaignConfig", *, user_data: str) -> list[str]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def terminate_all_tagged(self, campaign_name: str) -> int:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def list_active(self, campaign_name: str) -> list[dict]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def get_spot_price(self, region: str, instance_type: str) -> float:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)
