"""
Unit tests for Skolem IRI identity management.

Tests Skolem IRI minting and validation, plus OGM.fetch guard against anonymous nodes.
"""

import pytest

from kapps_triplestore_interface import IRI

from kapps_ogm.utils.skolem import (
    mint_skolem_iri,
    is_skolem_iri,
    DEFAULT_SKOLEM_NAMESPACE,
    WELL_KNOWN_GENID_PATH,
)
from kapps_ogm.utils.errors import AnonymousNodeFetchError
from kapps_ogm.ogm import OGM


class TestSkolemMinting:
    """Test Skolem IRI minting and validation functions."""

    def test_minted_iri_is_instance_and_contains_genid_path(self):
        """A minted IRI is an IRI instance and its string contains /.well-known/genid/."""
        result = mint_skolem_iri()
        assert isinstance(result, IRI)
        assert WELL_KNOWN_GENID_PATH in str(result)

    def test_two_consecutive_mints_differ(self):
        """Two consecutive mints produce different IRIs (no reuse)."""
        first = mint_skolem_iri()
        second = mint_skolem_iri()
        assert first != second

    def test_custom_namespace_is_honoured(self):
        """A custom namespace is honoured in the minted IRI."""
        custom_ns = "https://example.org/.well-known/genid/"
        result = mint_skolem_iri(namespace=custom_ns)
        assert str(result).startswith(custom_ns)

    def test_is_skolem_iri_true_for_minted_iri(self):
        """is_skolem_iri returns True for a minted IRI."""
        minted = mint_skolem_iri()
        assert is_skolem_iri(minted) is True

    def test_is_skolem_iri_true_for_known_genid_iri(self):
        """is_skolem_iri returns True for an IRI with the well-known genid path."""
        iri = IRI("https://w3id.org/circularfactory/.well-known/genid/ab12")
        assert is_skolem_iri(iri) is True

    def test_is_skolem_iri_false_for_ordinary_iri(self):
        """is_skolem_iri returns False for an ordinary IRI."""
        iri = IRI("https://example.org/instances/belt1")
        assert is_skolem_iri(iri) is False

    def test_is_skolem_iri_false_for_none(self):
        """is_skolem_iri returns False for None."""
        assert is_skolem_iri(None) is False

    def test_is_skolem_iri_false_for_plain_string(self):
        """is_skolem_iri returns False for a plain non-IRI string."""
        assert is_skolem_iri("https://example.org/.well-known/genid/xyz") is False

    def test_ogm_default_skolem_namespace(self, mock_db):
        """OGM default skolem_namespace equals DEFAULT_SKOLEM_NAMESPACE."""
        ogm = OGM(db=mock_db)
        assert ogm.skolem_namespace == DEFAULT_SKOLEM_NAMESPACE

    def test_ogm_custom_skolem_namespace(self, mock_db):
        """OGM stores a custom skolem_namespace when provided."""
        custom_ns = "https://example.org/.well-known/genid/"
        ogm = OGM(db=mock_db, skolem_namespace=custom_ns)
        assert ogm.skolem_namespace == custom_ns


class TestMintingAndRecognitionAgree:
    """Everything minted must be recognisable, or the fetch guard silently stops working.

    Only the minting *authority* is an open governance decision. The `/.well-known/genid/` path
    is fixed by RDF 1.1 Concepts section 3.5's recognisability provision, so a namespace that
    omits it is rejected rather than quietly producing unrecognisable addresses.
    """

    def test_a_namespace_without_the_well_known_path_is_rejected(self):
        with pytest.raises(ValueError, match=WELL_KNOWN_GENID_PATH):
            mint_skolem_iri("https://example.org/anon/")

    def test_the_ogm_rejects_such_a_namespace_at_construction(self, mock_db):
        """Fail when the OGM is built, not on the first anonymous write."""
        with pytest.raises(ValueError, match=WELL_KNOWN_GENID_PATH):
            OGM(db=mock_db, skolem_namespace="https://example.org/anon/")

    @pytest.mark.parametrize(
        "namespace",
        [
            DEFAULT_SKOLEM_NAMESPACE,
            "https://example.org/.well-known/genid/",
            "https://sfb1574.kit.edu/.well-known/genid/",
            "https://example.org/.well-known/genid",  # no trailing slash
        ],
    )
    def test_every_accepted_namespace_mints_a_recognisable_iri(self, namespace):
        assert is_skolem_iri(mint_skolem_iri(namespace))


class TestFetchGuardAgainstAnonymousNodes:
    """Test OGM.fetch guard against Skolem (anonymous) IRIs."""

    def test_fetch_skolem_iri_raises_anonymous_node_fetch_error(self, ogm_with_mock_db):
        """OGM.fetch raises AnonymousNodeFetchError when given a Skolem IRI."""
        skolem_iri = mint_skolem_iri()
        with pytest.raises(AnonymousNodeFetchError, match="fetch its parent"):
            ogm_with_mock_db.fetch(instance_iri=skolem_iri)

    def test_fetch_guard_fires_before_db_access(self, ogm_with_mock_db, mock_db):
        """The Skolem IRI guard fires before any database call."""
        skolem_iri = mint_skolem_iri()
        with pytest.raises(AnonymousNodeFetchError):
            ogm_with_mock_db.fetch(instance_iri=skolem_iri)
        assert mock_db.owl_get_classes_of_individual.call_count == 0

    def test_fetch_non_skolem_iri_does_not_trigger_guard(self, ogm_with_mock_db):
        """A non-Skolem IRI does NOT trigger the AnonymousNodeFetchError guard."""
        ordinary_iri = IRI("https://example.org/instances/belt1")
        with pytest.raises(Exception) as exc_info:
            ogm_with_mock_db.fetch(instance_iri=ordinary_iri)
        assert not isinstance(exc_info.value, AnonymousNodeFetchError)
