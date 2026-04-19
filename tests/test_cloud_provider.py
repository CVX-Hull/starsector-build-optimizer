"""Tests for cloud_provider.py — CloudProvider ABC, AWSProvider (moto), HetznerProvider stub.

The ABC surface is keyword-only:
    provision_fleet(*, fleet_name, project_tag, regions, ami_ids_by_region,
                    instance_types, ssh_key_name, spot_allocation_strategy,
                    target_workers, user_data) -> list[str]
    terminate_fleet(*, fleet_name, project_tag) -> int            # targeted
    terminate_all_tagged(project_tag) -> int                      # sweep backstop
    list_active(project_tag) -> list[dict]
    get_spot_price(region, instance_type) -> float

Every provisioned resource carries TWO tags: `Project=<project_tag>` AND
`Fleet=<fleet_name>`. `terminate_fleet` filters on both; `terminate_all_tagged`
filters on `Project` only.
"""

import base64

import pytest


PROBE_USER_DATA = "#!/bin/bash\nset -euo pipefail\necho probe-boot-ok > /var/log/starsector-probe.log\n"

# Canonical kwargs for provision_fleet across tests.
_PROVISION_KWARGS = dict(
    instance_types=("c7a.2xlarge",),
    ssh_key_name="starsector-probe",
    spot_allocation_strategy="price-capacity-optimized",
    target_workers=1,
    user_data=PROBE_USER_DATA,
)


def _provision(provider, *, fleet_name, project_tag, regions=("us-east-1",),
               ami_id="ami-00000000000000000", user_data=PROBE_USER_DATA,
               target_workers=1):
    return provider.provision_fleet(
        fleet_name=fleet_name,
        project_tag=project_tag,
        regions=regions,
        ami_ids_by_region={r: ami_id for r in regions},
        instance_types=("c7a.2xlarge",),
        ssh_key_name="starsector-probe",
        spot_allocation_strategy="price-capacity-optimized",
        target_workers=target_workers,
        user_data=user_data,
    )


class TestCloudProviderABC:
    def test_cloud_provider_is_abc(self):
        from starsector_optimizer.cloud_provider import CloudProvider
        with pytest.raises(TypeError):
            CloudProvider()

    def test_subclass_missing_methods_fails(self):
        from starsector_optimizer.cloud_provider import CloudProvider

        class Partial(CloudProvider):
            def provision_fleet(self, **kwargs):
                return []

            def terminate_fleet(self, *, fleet_name, project_tag):
                return 0

            def terminate_all_tagged(self, project_tag):
                return 0

            def list_active(self, project_tag):
                return []

        # missing get_spot_price
        with pytest.raises(TypeError):
            Partial()

    def test_old_create_fleet_method_gone(self):
        """Clean rewrite — create_fleet must not exist on the ABC or AWSProvider."""
        from starsector_optimizer.cloud_provider import AWSProvider, CloudProvider
        assert not hasattr(CloudProvider, "create_fleet")
        assert not hasattr(AWSProvider, "create_fleet")


class TestAWSProviderBasics:
    """moto-mocked AWS. Tests load-bearing boto3 interactions."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_list_active_empty_initially(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        assert provider.list_active("starsector-empty") == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminate_all_tagged_handles_empty(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        assert provider.terminate_all_tagged("starsector-empty") == 0

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminate_fleet_handles_empty(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        assert provider.terminate_fleet(
            fleet_name="probe", project_tag="starsector-empty",
        ) == 0

    @pytest.mark.usefixtures("aws_mocked")
    def test_get_spot_price_returns_float(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        price = provider.get_spot_price("us-east-1", "c7a.2xlarge")
        assert isinstance(price, float)
        assert price >= 0.0


class TestProvisionFleetNaming:
    """LT and SG names include both project_tag AND fleet_name for study isolation."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_lt_name_is_project_tag_plus_fleet(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="hammerhead__early__seed0",
                   project_tag="starsector-smoke")
        client = boto3.client("ec2", region_name="us-east-1")
        names = [
            lt["LaunchTemplateName"]
            for lt in client.describe_launch_templates()["LaunchTemplates"]
        ]
        assert "starsector-smoke__hammerhead__early__seed0" in names

    @pytest.mark.usefixtures("aws_mocked")
    def test_sg_name_is_project_tag_plus_fleet(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="alpha",
                   project_tag="starsector-ns")
        client = boto3.client("ec2", region_name="us-east-1")
        groups = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": ["starsector-ns__alpha"]}],
        )["SecurityGroups"]
        assert len(groups) == 1

    @pytest.mark.usefixtures("aws_mocked")
    def test_two_fleets_same_campaign_same_region_no_collision(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="fleetA", project_tag="starsector-multi")
        _provision(provider, fleet_name="fleetB", project_tag="starsector-multi")
        active = provider.list_active("starsector-multi")
        assert len(active) >= 2


class TestProvisionFleetTagging:
    @pytest.mark.usefixtures("aws_mocked")
    def test_instances_tagged_with_both_project_and_fleet(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        ids = _provision(provider, fleet_name="F1", project_tag="starsector-tag1")
        assert ids
        client = boto3.client("ec2", region_name="us-east-1")
        response = client.describe_instances(InstanceIds=ids)
        for reservation in response["Reservations"]:
            for inst in reservation["Instances"]:
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                assert tags.get("Project") == "starsector-tag1"
                assert tags.get("Fleet") == "F1"

    @pytest.mark.usefixtures("aws_mocked")
    def test_launch_template_tagged_with_both(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="LTfleet", project_tag="starsector-lt")
        client = boto3.client("ec2", region_name="us-east-1")
        # Tag-based describe (not name-based) — validates both tag applications.
        response = client.describe_launch_templates(
            Filters=[
                {"Name": "tag:Project", "Values": ["starsector-lt"]},
                {"Name": "tag:Fleet", "Values": ["LTfleet"]},
            ],
        )
        assert len(response["LaunchTemplates"]) == 1

    @pytest.mark.usefixtures("aws_mocked")
    def test_security_group_tagged_with_both(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="SGfleet", project_tag="starsector-sg")
        client = boto3.client("ec2", region_name="us-east-1")
        response = client.describe_security_groups(
            Filters=[
                {"Name": "tag:Project", "Values": ["starsector-sg"]},
                {"Name": "tag:Fleet", "Values": ["SGfleet"]},
            ],
        )
        assert len(response["SecurityGroups"]) == 1
        # Workers outbound-only: zero ingress rules.
        assert response["SecurityGroups"][0].get("IpPermissions", []) == []


class TestProvisionFleetUserData:
    @pytest.mark.usefixtures("aws_mocked")
    def test_user_data_embedded_base64(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="udf", project_tag="starsector-ud")
        client = boto3.client("ec2", region_name="us-east-1")
        versions = client.describe_launch_template_versions(
            LaunchTemplateName="starsector-ud__udf",
        )["LaunchTemplateVersions"]
        encoded = versions[0]["LaunchTemplateData"].get("UserData", "")
        assert "probe-boot-ok" in base64.b64decode(encoded).decode("utf-8")

    @pytest.mark.usefixtures("aws_mocked")
    def test_second_provision_same_fleet_name_appends_version(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="v", project_tag="starsector-ver",
                   user_data=PROBE_USER_DATA)
        _provision(provider, fleet_name="v", project_tag="starsector-ver",
                   user_data=PROBE_USER_DATA + "# v2\n")
        client = boto3.client("ec2", region_name="us-east-1")
        versions = client.describe_launch_template_versions(
            LaunchTemplateName="starsector-ver__v",
        )["LaunchTemplateVersions"]
        assert len(versions) >= 2


class TestProvisionFleetNoCampaignConfigDependency:
    """Clean rewrite: provider must NOT accept a CampaignConfig parameter."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_provision_fleet_rejects_positional_campaign_config(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        # Old create_fleet(config, *, user_data) signature MUST NOT work.
        with pytest.raises(TypeError):
            provider.provision_fleet(object(), user_data=PROBE_USER_DATA)


class TestTerminateFleetTargeted:
    """terminate_fleet reaps ONLY the matching fleet within a project tag."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_terminates_only_matching_fleet(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        ids_a = _provision(provider, fleet_name="A", project_tag="starsector-t1")
        ids_b = _provision(provider, fleet_name="B", project_tag="starsector-t1")
        assert ids_a and ids_b
        reaped = provider.terminate_fleet(
            fleet_name="A", project_tag="starsector-t1",
        )
        assert reaped == len(ids_a)
        # Fleet B instances must still be listed active.
        active = provider.list_active("starsector-t1")
        active_ids = {inst["id"] for inst in active}
        assert set(ids_b) & active_ids == set(ids_b)
        assert set(ids_a) & active_ids == set()

    @pytest.mark.usefixtures("aws_mocked")
    def test_deletes_only_matching_launch_template(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="A", project_tag="starsector-t2")
        _provision(provider, fleet_name="B", project_tag="starsector-t2")
        provider.terminate_fleet(fleet_name="A", project_tag="starsector-t2")
        client = boto3.client("ec2", region_name="us-east-1")
        names = [
            lt["LaunchTemplateName"]
            for lt in client.describe_launch_templates()["LaunchTemplates"]
        ]
        assert "starsector-t2__A" not in names
        assert "starsector-t2__B" in names

    @pytest.mark.usefixtures("aws_mocked")
    def test_deletes_only_matching_security_group(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="A", project_tag="starsector-t3")
        _provision(provider, fleet_name="B", project_tag="starsector-t3")
        provider.terminate_fleet(fleet_name="A", project_tag="starsector-t3")
        client = boto3.client("ec2", region_name="us-east-1")
        all_sgs = client.describe_security_groups(
            Filters=[{"Name": "tag:Project", "Values": ["starsector-t3"]}],
        )["SecurityGroups"]
        names = {sg["GroupName"] for sg in all_sgs}
        assert "starsector-t3__A" not in names
        assert "starsector-t3__B" in names

    @pytest.mark.usefixtures("aws_mocked")
    def test_idempotent_second_call(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="F", project_tag="starsector-t4")
        provider.terminate_fleet(fleet_name="F", project_tag="starsector-t4")
        assert provider.terminate_fleet(
            fleet_name="F", project_tag="starsector-t4",
        ) == 0


class TestTerminateAllTaggedSweep:
    """terminate_all_tagged reaps every fleet within a project tag."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_sweep_reaps_multiple_fleets(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        ids_a = _provision(provider, fleet_name="A", project_tag="starsector-sweep")
        ids_b = _provision(provider, fleet_name="B", project_tag="starsector-sweep")
        reaped = provider.terminate_all_tagged("starsector-sweep")
        assert reaped == len(ids_a) + len(ids_b)
        assert provider.list_active("starsector-sweep") == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_sweep_deletes_all_tagged_launch_templates(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="A", project_tag="starsector-sweeplt")
        _provision(provider, fleet_name="B", project_tag="starsector-sweeplt")
        provider.terminate_all_tagged("starsector-sweeplt")
        client = boto3.client("ec2", region_name="us-east-1")
        remaining = client.describe_launch_templates(
            Filters=[{"Name": "tag:Project", "Values": ["starsector-sweeplt"]}],
        )["LaunchTemplates"]
        assert remaining == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_sweep_deletes_all_tagged_security_groups(self):
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="A", project_tag="starsector-sweepsg")
        _provision(provider, fleet_name="B", project_tag="starsector-sweepsg")
        provider.terminate_all_tagged("starsector-sweepsg")
        client = boto3.client("ec2", region_name="us-east-1")
        remaining = client.describe_security_groups(
            Filters=[{"Name": "tag:Project", "Values": ["starsector-sweepsg"]}],
        )["SecurityGroups"]
        assert remaining == []

    @pytest.mark.usefixtures("aws_mocked")
    def test_sweep_idempotent(self):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        _provision(provider, fleet_name="F", project_tag="starsector-swidem")
        provider.terminate_all_tagged("starsector-swidem")
        assert provider.terminate_all_tagged("starsector-swidem") == 0


class TestDeletionTagFiltering:
    """The name-vs-tag filter change: tag-based lookup finds renamed resources."""

    @pytest.mark.usefixtures("aws_mocked")
    def test_launch_template_deletion_filters_by_tag(self):
        """LT with a non-matching name but matching Project tag must still be
        reaped by terminate_all_tagged. Proves the delete uses tag, not name."""
        import boto3
        from starsector_optimizer.cloud_provider import AWSProvider
        # Manually pre-create a tagged LT with a different name.
        client = boto3.client("ec2", region_name="us-east-1")
        client.create_launch_template(
            LaunchTemplateName="legacy-orphan-lt",
            LaunchTemplateData={"ImageId": "ami-00000000000000000"},
            TagSpecifications=[{
                "ResourceType": "launch-template",
                "Tags": [{"Key": "Project", "Value": "starsector-orphan"}],
            }],
        )
        provider = AWSProvider(regions=("us-east-1",))
        provider.terminate_all_tagged("starsector-orphan")
        names = [
            lt["LaunchTemplateName"]
            for lt in client.describe_launch_templates()["LaunchTemplates"]
        ]
        assert "legacy-orphan-lt" not in names


class TestFleetProvisionSGPropagation:
    """Concurrent provisioning trips InvalidGroup.NotFound in create_fleet
    because the Fleet service has a replication lag after SG creation. The
    provider (a) blocks on `security_group_exists` waiter post-create, and
    (b) retries create_fleet on InvalidGroup.NotFound for a few seconds."""

    def _mock_client(self, *, fleet_responses):
        """Build a MagicMock boto3 EC2 client that returns the supplied
        sequence of create_fleet responses and otherwise behaves as a stub
        for the calls _ensure_security_group + _ensure_launch_template make."""
        from unittest.mock import MagicMock
        client = MagicMock()
        client.describe_security_groups.return_value = {"SecurityGroups": []}
        client.create_security_group.return_value = {"GroupId": "sg-AAAA"}
        # The waiter must be called; track invocations.
        waiter = MagicMock()
        client.get_waiter.return_value = waiter
        client.describe_launch_templates.return_value = {"LaunchTemplates": []}
        client.create_launch_template.return_value = {}
        client.create_fleet.side_effect = fleet_responses
        return client, waiter

    def test_sg_visibility_waiter_invoked_after_create(self, monkeypatch):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        client, waiter = self._mock_client(fleet_responses=[{
            "Instances": [{"InstanceIds": ["i-0000000000000000"]}],
            "Errors": [],
        }])
        monkeypatch.setattr(provider, "_client", lambda region: client)
        _provision(provider, fleet_name="f", project_tag="starsector-p")
        client.get_waiter.assert_called_once_with("security_group_exists")
        waiter.wait.assert_called_once()
        # Waiter kwargs include the freshly-minted SG id.
        assert waiter.wait.call_args.kwargs["GroupIds"] == ["sg-AAAA"]

    def test_create_fleet_retries_on_invalid_group_not_found(self, monkeypatch):
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        # First attempt: transient SG NotFound. Second: success.
        transient = {
            "Instances": [],
            "Errors": [{"ErrorCode": "InvalidGroup.NotFound",
                        "ErrorMessage": "propagation lag"}],
        }
        success = {
            "Instances": [{"InstanceIds": ["i-1111111111111111"]}],
            "Errors": [],
        }
        client, _ = self._mock_client(fleet_responses=[transient, success])
        monkeypatch.setattr(provider, "_client", lambda region: client)
        monkeypatch.setattr(
            "starsector_optimizer.cloud_provider.time.sleep",
            lambda s: None,  # collapse the retry delay in tests
        )
        ids = _provision(provider, fleet_name="f", project_tag="starsector-p")
        assert ids == ["i-1111111111111111"]
        assert client.create_fleet.call_count == 2

    def test_create_fleet_retries_when_transient_co_occurs_with_permanent(self, monkeypatch):
        """Production scenario: us-east-1e rejects c7a.2xlarge (permanent
        InvalidFleetConfiguration) at the same time other AZs emit transient
        InvalidGroup.NotFound for SG-propagation lag. Retry must fire on the
        transient ones; the 1e rejection is OK to accept as a partial-error."""
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        mixed_transient = {
            "Instances": [],
            "Errors": [
                {"ErrorCode": "InvalidFleetConfiguration",
                 "ErrorMessage": "c7a.2xlarge not in us-east-1e"},
                {"ErrorCode": "InvalidGroup.NotFound",
                 "ErrorMessage": "propagation lag"},
            ],
        }
        # Second attempt succeeds on the non-1e AZs; 1e still throws but we
        # got one instance so we return happily.
        mixed_recovered = {
            "Instances": [{"InstanceIds": ["i-2222222222222222"]}],
            "Errors": [
                {"ErrorCode": "InvalidFleetConfiguration",
                 "ErrorMessage": "c7a.2xlarge not in us-east-1e"},
            ],
        }
        client, _ = self._mock_client(fleet_responses=[mixed_transient, mixed_recovered])
        monkeypatch.setattr(provider, "_client", lambda region: client)
        monkeypatch.setattr(
            "starsector_optimizer.cloud_provider.time.sleep",
            lambda s: None,
        )
        ids = _provision(provider, fleet_name="f", project_tag="starsector-p")
        assert ids == ["i-2222222222222222"]
        assert client.create_fleet.call_count == 2

    def test_create_fleet_retries_on_invalid_launch_template_not_found(self, monkeypatch):
        """Same replication-lag pattern as SG but for LT. Under concurrent
        provisioning, create_fleet can see `InvalidLaunchTemplateName.
        NotFoundException` for an LT we just created. Must retry."""
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        transient_lt = {
            "Instances": [],
            "Errors": [{"ErrorCode": "InvalidLaunchTemplateName.NotFoundException",
                        "ErrorMessage": "LT replication lag"}],
        }
        success = {
            "Instances": [{"InstanceIds": ["i-3333333333333333"]}],
            "Errors": [],
        }
        client, _ = self._mock_client(fleet_responses=[transient_lt, success])
        monkeypatch.setattr(provider, "_client", lambda region: client)
        monkeypatch.setattr(
            "starsector_optimizer.cloud_provider.time.sleep",
            lambda s: None,
        )
        ids = _provision(provider, fleet_name="f", project_tag="starsector-p")
        assert ids == ["i-3333333333333333"]
        assert client.create_fleet.call_count == 2

    def test_create_fleet_does_not_retry_on_unrelated_errors(self, monkeypatch):
        """us-east-1e: c7a.2xlarge unsupported — that's permanent, not transient.
        If ALL errors are permanent we do NOT retry."""
        from starsector_optimizer.cloud_provider import AWSProvider
        provider = AWSProvider(regions=("us-east-1",))
        permanent = {
            "Instances": [],
            "Errors": [{"ErrorCode": "InvalidFleetConfiguration",
                        "ErrorMessage": "instance-type not in AZ"}],
        }
        client, _ = self._mock_client(fleet_responses=[permanent])
        monkeypatch.setattr(provider, "_client", lambda region: client)
        monkeypatch.setattr(
            "starsector_optimizer.cloud_provider.time.sleep",
            lambda s: None,
        )
        with pytest.raises(RuntimeError, match="zero instances"):
            _provision(provider, fleet_name="f", project_tag="starsector-p")
        assert client.create_fleet.call_count == 1

    def test_create_fleet_retry_capped(self, monkeypatch):
        """If SG propagation never resolves, we stop after the retry budget."""
        from starsector_optimizer.cloud_provider import (
            AWSProvider, _FLEET_PROVISION_MAX_RETRIES,
        )
        provider = AWSProvider(regions=("us-east-1",))
        transient = {
            "Instances": [],
            "Errors": [{"ErrorCode": "InvalidGroup.NotFound",
                        "ErrorMessage": "lag"}],
        }
        # Feed the same transient response forever.
        client, _ = self._mock_client(
            fleet_responses=[transient] * (_FLEET_PROVISION_MAX_RETRIES + 2),
        )
        monkeypatch.setattr(provider, "_client", lambda region: client)
        monkeypatch.setattr(
            "starsector_optimizer.cloud_provider.time.sleep",
            lambda s: None,
        )
        with pytest.raises(RuntimeError, match="zero instances"):
            _provision(provider, fleet_name="f", project_tag="starsector-p")
        assert client.create_fleet.call_count == _FLEET_PROVISION_MAX_RETRIES


class TestHetznerProvider:
    """HetznerProvider is a stub; every method raises NotImplementedError."""

    def test_provision_fleet_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        provider = HetznerProvider()
        with pytest.raises(NotImplementedError, match=r"\$500"):
            provider.provision_fleet(
                fleet_name="x", project_tag="starsector-x",
                regions=("eu-central",), ami_ids_by_region={"eu-central": "img-0"},
                instance_types=("ccx33",), ssh_key_name="k",
                spot_allocation_strategy="price-capacity-optimized",
                target_workers=1, user_data="",
            )

    def test_terminate_fleet_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        with pytest.raises(NotImplementedError):
            HetznerProvider().terminate_fleet(fleet_name="x", project_tag="starsector-x")

    def test_terminate_all_tagged_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        with pytest.raises(NotImplementedError):
            HetznerProvider().terminate_all_tagged("starsector-anything")

    def test_list_active_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        with pytest.raises(NotImplementedError):
            HetznerProvider().list_active("starsector-anything")

    def test_get_spot_price_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        with pytest.raises(NotImplementedError):
            HetznerProvider().get_spot_price("eu-central", "ccx33")
