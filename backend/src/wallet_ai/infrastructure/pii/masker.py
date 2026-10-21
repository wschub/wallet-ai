import re
from uuid import UUID

from wallet_ai.domain.ports.pii_service import PIIService


class PIIMasker(PIIService):
    EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
    PHONE_COL = re.compile(r"(\+?57[\s.\-()]*)?(\(?3[0-9]{2}\)?[\s.\-]*[0-9]{3}[\s.\-]*[0-9]{4})")
    CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
    CC_WITH_CONTEXT = re.compile(
        r"\b(c[ée]dula|cc|documento|identificaci[óo]n)\b[^0-9]{0,12}([0-9](?:[0-9.\s-]){5,11}[0-9])",
        re.IGNORECASE,
    )
    CC_ONLY_NUM = re.compile(r"\b([0-9](?:[0-9.\s-]){5,11}[0-9])\b")

    def mask(self, text: str) -> str:
        if not text:
            return text

        masked = text

        # Email
        masked = self.EMAIL.sub("<EMAIL>", masked)

        # Card with Luhn check
        def replace_card(m: re.Match) -> str:
            d = re.sub(r"[^0-9]", "", m.group(0))
            if len(d) < 13 or len(d) > 19:
                return m.group(0)
            if not self._luhn(d):
                return m.group(0)
            return "<CARD>"

        masked = self.CARD.sub(replace_card, masked)

        # Phone Colombia
        masked = self.PHONE_COL.sub("<PHONE>", masked)

        # ID with context
        masked = self.CC_WITH_CONTEXT.sub(lambda m: f"{m.group(1)} <ID>", masked)

        # Avoid UUID/money false positives for bare numbers
        def replace_bare_id(m: re.Match) -> str:
            num_raw = m.group(1)
            num = re.sub(r"[^0-9]", "", num_raw)
            if self._looks_like_uuid(num_raw):
                return num_raw
            if len(num) < 6:
                return num_raw
            if self._looks_like_money_or_date(num):
                return num_raw
            # Only mask if it looks like a cedula with context was already handled
            # Numbers without context should not be masked
            return num_raw

        masked = self.CC_ONLY_NUM.sub(replace_bare_id, masked)

        return masked

    @staticmethod
    def _luhn(num: str) -> bool:
        s = 0
        reverse = num[::-1]
        for i, c in enumerate(reverse):
            d = int(c)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            s += d
        return s % 10 == 0

    @staticmethod
    def _looks_like_uuid(num: str) -> bool:
        try:
            UUID(num)
            return True
        except Exception:
            return False

    @staticmethod
    def _looks_like_money_or_date(num: str) -> bool:
        return num.startswith("20") or num.startswith("19")
