"""Tests for cloud_provider.py — CloudProvider ABC, AWSProvider (moto), HetznerProvider stub."""

import abc

import pytest


class TestCloudProviderABC:
    def test_cloud_provider_is_abc(self):
        from starsector_optimizer.cloud_provider import CloudProvider
        with pytest.raises(TypeError):
            CloudProvider()

    def test_subclass_missing_methods_fails(self):
        from starsector_optimizer.cloud_provider import CloudProvider

        class Partial(CloudProvider):
            def create_fleet(self, config):
                return []

            def terminate_all_tagged(self, name):
                return 0

            def list_active(self, name):
                return []

        # missing get_spot_price
        with pytest.raises(TypeError):
            Partial()


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


class TestHetznerProvider:
    """HetznerProvider is a stub; every method raises NotImplementedError."""

    def test_create_fleet_raises(self):
        from starsector_optimizer.cloud_provider import HetznerProvider
        provider = HetznerProvider()
        with pytest.raises(NotImplementedError, match="\\$500"):
            provider.create_fleet(None)

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
