"""Tests for cloud_provider.py — CloudProvider ABC, AWSProvider (moto), HetznerProvider stub."""

import base64

import pytest


PROBE_USER_DATA = "#!/bin/bash\nset -euo pipefail\necho probe-boot-ok > /var/log/starsector-probe.log\n"


class TestCloudProviderABC:
    def test_cloud_provider_is_abc(self):
        from starsector_optimizer.cloud_provider import CloudProvider
        with pytest.raises(TypeError):
            CloudProvider()

    def test_subclass_missing_methods_fails(self):
        from starsector_optimizer.cloud_provider import CloudProvider

        class Partial(CloudProvider):
            def create_fleet(self, config, *, user_data):
                return []

            def terminate_all_tagged(self, name):
                return 0

            def list_active(self, name):
                return []

        # missing get_spot_price
        with pytest.raises(TypeError):
            Partial()


def _make_minimal_campaign(name="test-campaign", regions=("us-east-1",)):
    from starsector_optimizer.models import (
        CampaignConfig, StudyConfig, GlobalAutoStopConfig,
    )
    return CampaignConfig(
        name=name,
        budget_usd=1.0,
        provider="aws",
        regions=regions,
        instance_types=("c7a.2xlarge",),
        spot_allocation_strategy="price-capacity-optimized",
        capacity_rebalancing=True,
        max_concurrent_workers=2,
        min_workers_to_start=1,
        partial_fleet_policy="abort",
        ami_ids_by_region={r: "ami-00000000000000000" for r in regions},
        ssh_key_name="starsector-probe",
        tailscale_authkey_secret="tskey-sentinel",
        studies=(),
        global_auto_stop=GlobalAutoStopConfig(),
    )


class TestAWSProvider:
    """moto-mocked AWS. Tests load-bearing boto3 interactions."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_list_active_empty_initially(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        assert provider.list_active("test-campaign") == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminate_all_tagged_handles_empty(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        count = provider.terminate_all_tagged("test-campaign")
        assert count == 0

    @pytest.mark.usefixtures("aws_mocked")
    def test_get_spot_price_returns_float(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        price = provider.get_spot_price("us-east-1", "c7a.2xlarge")
        assert isinstance(price, float)
        assert price >= 0.0


class TestAWSProviderCreateFleet:
    """End-to-end create_fleet: LaunchTemplate + SecurityGroup + instance IDs."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_create_fleet_returns_instance_ids(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign()
        provider = AWSProvider(regions=config.regions)
        ids = provider.create_fleet(config, user_data=PROBE_USER_DATA)
        assert isinstance(ids, list)
        # moto create_fleet returns at least one instance for TotalTargetCapacity>=1.
        assert len(ids) >= 1
        for iid in ids:
            assert iid.startswith("i-")

    @pytest.mark.usefixtures("aws_mocked")
    def test_create_fleet_creates_launch_template(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="lt-check")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        client = boto3.client("ec2", region_name="us-east-1")
        response = client.describe_launch_templates(
            LaunchTemplateNames=["starsector-lt-check"],
        )
        assert len(response["LaunchTemplates"]) == 1

    @pytest.mark.usefixtures("aws_mocked")
    def test_create_fleet_creates_security_group(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="sg-check")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        client = boto3.client("ec2", region_name="us-east-1")
        response = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": ["starsector-sg-check"]}],
        )
        assert len(response["SecurityGroups"]) == 1
        # Workers are outbound-only: zero ingress rules.
        assert response["SecurityGroups"][0].get("IpPermissions", []) == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_create_fleet_tags_all_resources(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="tag-check")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        client = boto3.client("ec2", region_name="us-east-1")
        # LT tagged
        lt = client.describe_launch_templates(
            LaunchTemplateNames=["starsector-tag-check"],
        )["LaunchTemplates"][0]
        tags = {t["Key"]: t["Value"] for t in lt.get("Tags", [])}
        assert tags.get("Project") == "starsector-tag-check"
        # SG tagged
        sg = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": ["starsector-tag-check"]}],
        )["SecurityGroups"][0]
        sg_tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
        assert sg_tags.get("Project") == "starsector-tag-check"

    @pytest.mark.usefixtures("aws_mocked")
    def test_create_fleet_embeds_user_data(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="userdata-check")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        client = boto3.client("ec2", region_name="us-east-1")
        response = client.describe_launch_template_versions(
            LaunchTemplateName="starsector-userdata-check",
        )
        latest = response["LaunchTemplateVersions"][0]
        encoded = latest["LaunchTemplateData"].get("UserData", "")
        decoded = base64.b64decode(encoded).decode("utf-8")
        assert "probe-boot-ok" in decoded

    @pytest.mark.usefixtures("aws_mocked")
    def test_create_fleet_idempotent_second_call_updates_version(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="idem-check")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        provider.create_fleet(config, user_data=PROBE_USER_DATA + "# v2\n")
        client = boto3.client("ec2", region_name="us-east-1")
        versions = client.describe_launch_template_versions(
            LaunchTemplateName="starsector-idem-check",
        )["LaunchTemplateVersions"]
        # At least 2 versions; second call created a new version.
        assert len(versions) >= 2

    @pytest.mark.usefixtures("aws_mocked")
    def test_list_active_returns_provisioned_instances(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="active-check")
        provider = AWSProvider(regions=config.regions)
        ids = provider.create_fleet(config, user_data=PROBE_USER_DATA)
        active = provider.list_active("active-check")
        assert len(active) == len(ids)


class TestAWSProviderTerminate:
    """terminate_all_tagged reaps instances, LTs, and SGs."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminate_removes_instances(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="teardown1")
        provider = AWSProvider(regions=config.regions)
        ids = provider.create_fleet(config, user_data=PROBE_USER_DATA)
        assert len(ids) >= 1
        count = provider.terminate_all_tagged("teardown1")
        assert count == len(ids)
        assert provider.list_active("teardown1") == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminate_deletes_launch_template(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="teardown2")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        provider.terminate_all_tagged("teardown2")
        client = boto3.client("ec2", region_name="us-east-1")
        response = client.describe_launch_templates()
        names = [lt["LaunchTemplateName"] for lt in response["LaunchTemplates"]]
        assert "starsector-teardown2" not in names

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminate_deletes_security_group(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="teardown3")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        provider.terminate_all_tagged("teardown3")
        client = boto3.client("ec2", region_name="us-east-1")
        response = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": ["starsector-teardown3"]}],
        )
        assert response["SecurityGroups"] == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminate_idempotent_second_call(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        config = _make_minimal_campaign(name="teardown4")
        provider = AWSProvider(regions=config.regions)
        provider.create_fleet(config, user_data=PROBE_USER_DATA)
        provider.terminate_all_tagged("teardown4")
        # Second call must not raise.
        second = provider.terminate_all_tagged("teardown4")
        assert second == 0


class TestHetznerProvider:
    """HetznerProvider is a stub; every method raises NotImplementedError."""

    def test_create_fleet_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        provider = HetznerProvider()
        with pytest.raises(NotImplementedError, match="\\$500"):
            provider.create_fleet(None, user_data="")

    def test_terminate_all_tagged_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        provider = HetznerProvider()
        with pytest.raises(NotImplementedError):
            provider.terminate_all_tagged("anything")

    def test_list_active_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        provider = HetznerProvider()
        with pytest.raises(NotImplementedError):
            provider.list_active("anything")

    def test_get_spot_price_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        provider = HetznerProvider()
        with pytest.raises(NotImplementedError):
            provider.get_spot_price("eu-central", "ccx33")
