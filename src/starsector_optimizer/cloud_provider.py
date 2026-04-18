"""Cloud provider abstraction — AWS-only MVP; Hetzner stubbed for future.

See docs/specs/22-cloud-deployment.md. Uses boto3 directly (no Libcloud);
the abstraction is a narrow ABC so a Libcloud wrapper can slot in later
without refactoring callers.
"""

from __future__ import annotations

import abc
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
    def create_fleet(self, config: "CampaignConfig") -> list[str]:
        """Launch a diversified spot fleet. Return instance IDs that ARE UP."""

    @abc.abstractmethod
    def terminate_all_tagged(self, campaign_name: str) -> int:
        """Terminate every instance tagged Project=starsector-<campaign_name>.
        Return count terminated. Idempotent."""

    @abc.abstractmethod
    def list_active(self, campaign_name: str) -> list[dict]:
        """Return RUNNING + PENDING instances tagged for this campaign."""

    @abc.abstractmethod
    def get_spot_price(self, region: str, instance_type: str) -> float:
        """Most-recent observed spot price in USD/hour."""


# ---- AWS ---------------------------------------------------------------------


def _project_tag(campaign_name: str) -> str:
    return f"{_TAG_PREFIX}-{campaign_name}"


class AWSProvider(CloudProvider):
    """AWS EC2 spot provider using boto3 directly.

    create_fleet uses EC2 Fleet with price-capacity-optimized + Capacity
    Rebalancing diversified across config.instance_types and config.regions.
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

    def create_fleet(self, config: "CampaignConfig") -> list[str]:
        """Launch the fleet. One EC2 Fleet request per region."""
        instance_ids: list[str] = []
        per_region_target = max(
            1, config.max_concurrent_workers // max(1, len(self._regions))
        )
        tag = _project_tag(config.name)
        for region in self._regions:
            client = self._client(region)
            ami_id = config.ami_ids_by_region.get(region)
            if not ami_id:
                logger.warning("create_fleet: no AMI id for region=%s; skipping", region)
                continue
            launch_template_configs = [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateName": tag,
                        "Version": "$Latest",
                    },
                    "Overrides": [
                        {"InstanceType": itype}
                        for itype in config.instance_types
                    ],
                },
            ]
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
                    "TotalTargetCapacity": per_region_target,
                    "DefaultTargetCapacityType": "spot",
                },
                LaunchTemplateConfigs=launch_template_configs,
                Type="instant",
                TagSpecifications=[{
                    "ResourceType": "instance",
                    "Tags": [{"Key": _TAG_KEY, "Value": tag}],
                }],
            )
            for instance in response.get("Instances", []):
                instance_ids.extend(instance.get("InstanceIds", []))
        return instance_ids

    def terminate_all_tagged(self, campaign_name: str) -> int:
        """Idempotent. Terminates across every region this provider knows about."""
        tag = _project_tag(campaign_name)
        total = 0
        for region in self._regions:
            client = self._client(region)
            ids = [inst["id"] for inst in self._describe_active(region, tag)]
            if not ids:
                continue
            client.terminate_instances(InstanceIds=ids)
            total += len(ids)
            logger.info("terminated %d instances in %s for tag=%s",
                        len(ids), region, tag)
        return total

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

    def create_fleet(self, config: "CampaignConfig") -> list[str]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def terminate_all_tagged(self, campaign_name: str) -> int:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def list_active(self, campaign_name: str) -> list[dict]:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)

    def get_spot_price(self, region: str, instance_type: str) -> float:
        raise NotImplementedError(_HETZNER_STUB_MESSAGE)
