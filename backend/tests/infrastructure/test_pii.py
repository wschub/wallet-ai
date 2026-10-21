import pytest

from wallet_ai.infrastructure.pii.masker import PIIMasker


def test_pii_masker_idempotent():
    m = PIIMasker()
    text = "contacto test@example.com con tarjeta 4111111111111111"
    masked1 = m.mask(text)
    masked2 = m.mask(masked1)
    assert masked1 == masked2


def test_pii_masker_email_phone_card():
    m = PIIMasker()
    text = "mail a.b+c@dominio.com.co y tel +573001112233 o 3001112233 y tarjeta 4111111111111111"
    masked = m.mask(text)
    assert "test@example.com" not in masked and "a.b+c@dominio.com.co" not in masked
    assert "4111111111111111" not in masked
    assert "<EMAIL>" in masked
    assert "<PHONE>" in masked
    assert "<CARD>" in masked


def test_pii_masker_cc_only_with_context():
    m = PIIMasker()
    assert m.mask("12345678") == "12345678"  # no context
    assert "<ID>" in m.mask("mi cédula es 12345678")
    assert "<ID>" in m.mask("CC 12345678")
    assert "<ID>" in m.mask("documento 12345678")


def test_pii_false_positives():
    m = PIIMasker()
    assert m.mask("50000") == "50000"
    assert m.mask("2025-10-20") == "2025-10-20"
    assert m.mask("e7a62f43-429a-4f6d-b8c0-2ab9e7d83012") == "e7a62f43-429a-4f6d-b8c0-2ab9e7d83012"


@pytest.mark.parametrize(
    "card,expect_masked",
    [
        ("4111 1111 1111 1111", True),
        ("4111-1111-1111-1111", True),
        ("4111111111111112", False),  # fails Luhn
    ],
)
def test_pii_card_formats(card, expect_masked):
    m = PIIMasker()
    masked = m.mask(card)
    if expect_masked:
        assert "<CARD>" in masked
        assert card.replace(" ", "").replace("-", "") not in masked
    else:
        assert masked == card or "<CARD>" not in masked


@pytest.mark.parametrize(
    "phone",
    ["+57 300 123 4567", "3001234567", "(300) 123-4567"],
)
def test_pii_colombian_phones(phone):
    m = PIIMasker()
    masked = m.mask(phone)
    assert "<PHONE>" in masked


@pytest.mark.parametrize(
    "text,masked",
    [
        ("CC 1234567890", True),
        ("cédula: 1.234.567.890", True),
        ("1234567890", False),
    ],
)
def test_pii_cc_context(text, masked):
    m = PIIMasker()
    result = m.mask(text)
    if masked:
        assert "<ID>" in result
    else:
        assert result == text


def test_pii_mask_idempotent_all_classes():
    m = PIIMasker()
    text = "correo test@example.com tel +573001112233 CC 123456789 tarjeta 4111111111111111"
    m1 = m.mask(text)
    m2 = m.mask(m1)
    assert m1 == m2


def test_pii_no_pii_unchanged():
    m = PIIMasker()
    text = "Hola mundo, sin datos sensibles aquí."
    assert m.mask(text) == text
