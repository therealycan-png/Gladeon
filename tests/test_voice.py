"""Pipeline tests for the voice layer, from spec v2.13 section 17 (P-numbers in test names)."""
import unittest

from gladeon.voice import replies
from gladeon.voice.normalize import normalize
from gladeon.voice.router import (BRAIN_RAG, CANDIDATE, FINANCE_DEFERRED, GENERAL_CHAT, LOCAL_COMMAND,
                                  LOCK_HINT, NOT_IN_NOTES, PERSONAL, STILL_READING, VAULT_LOCKED,
                                  FollowUp, classify, route)
from gladeon.voice.tts import to_tts


def kind(text):
    return classify(normalize(text).text).kind


class Normalization(unittest.TestCase):
    def test_P1_alias_stripped(self):
        self.assertEqual(normalize("Hey Gladiator, who are you?").text, "who are you?")

    def test_P2_empty_wake(self):
        n = normalize("Gladeon.")
        self.assertTrue(n.empty)

    def test_P2b_empty_after_mode_and_echo(self):
        n = normalize("Gladeon, deep mode.")
        self.assertTrue(n.empty)
        self.assertEqual(n.mode, "deep")
        self.assertTrue(normalize("Gladeon Gladeon").empty)

    def test_P3_midsentence_gladiator_kept(self):
        t = "Tell me a short fact about a Roman gladiator."
        self.assertEqual(normalize(t).text, t)

    def test_P4_gladiator_movie_kept(self):
        self.assertTrue(normalize("Gladiator movie, who directed it?").text.startswith("Gladiator movie"))

    def test_P13_deep_mode_phrase(self):
        n = normalize("Think harder, what is entropy?")
        self.assertEqual((n.mode, n.text), ("deep", "what is entropy?"))

    def test_hygiene_and_entities(self):
        n = normalize("Gladeon,​ how much is in my G cash‮?")
        self.assertEqual(n.text, "how much is in my GCash?")

    def test_search_copy_folds_diacritics(self):
        self.assertEqual(normalize("Parañaque address").search, "Paranaque address")


class FinanceGuard(unittest.TestCase):
    POSITIVES = [
        "How much did I spend on GCash today?", "Magkano nagastos ko sa Grab?", "Eh yung utang ko?",
        "How much did I pay for rent?", "Magkano ang bill ko sa Meralco?", "Magkano binayad ko sa kuryente?",
        "Magkano na ang naipon kong pera?", "What's my account balance?", "How much money do I have?",
        "How much is left in my GCash?", "Magkano pa laman ng GCash ko?",
        "I've spent too much this week, how much was it?", "How much did I save last month?",
        "How much have I saved?", "Nagbayad na ba ako ng kuryente?", "Did I pay Maya for the cake?",
        "May pera pa ba ako?", "Do I have enough money for rent?", "Am I over budget?",
        "How much is my rent?", "Magkano ang upa ko?", "Can I afford the rent this month?",
        "Bayad na ba ako sa upa?", "Did I get paid this week?", "How much did I lend Mark?",
        "Magkano ipinahiram ko kay Mark?", "How much is our rent?", "How much did we spend on groceries?",
        "How much does Mark owe me?", "When do I get paid?", "May utang pa ba si Mark sa akin?",
        "Nagbayad na ba si Mark sa akin?", "How often do I get paid?", "How soon will I get paid?",
    ]
    NEGATIVES = [
        "Convert 100 dollars to pesos", "Ano pa natira sa to-do list ko?", "What's my GCash number in my notes?",
        "Bill Gates net worth", "work-life balance tips", "Give me work-life balance tips",
        "How much water should I drink a day?", "How much sleep do I need?", "How do I pay with GCash?",
        "How many pesos will I get for 100 dollars?", "Magkano ang pamasahe natin papuntang Baguio?",
        "What should I spend my birthday doing?", "How do I get paid on PayPal?",
    ]

    def test_P7_positives(self):
        for t in self.POSITIVES:
            with self.subTest(t=t):
                self.assertEqual(kind(t), FINANCE_DEFERRED)

    def test_P8_negatives(self):
        for t in self.NEGATIVES:
            with self.subTest(t=t):
                self.assertNotEqual(kind(t), FINANCE_DEFERRED)


class PersonalReference(unittest.TestCase):
    LOCKED_POSITIVES = [
        "What did I write about the landlord?", "Where did I put my passport?", "Ano ang wifi password ko?",
        "Ano ang aking wifi password?", "Saan ko nilagay ang passport?", "Saan ko ba nilagay ang passport?",
        "I've noted the plate number, what is it?", "What is my neighbor's Wi-Fi password?",
    ]
    NEGATIVES = [
        "Help me write a cover letter", "How do I save a file as PDF?", "List the planets for me",
        "Why does my code throw KeyError?", "Paano i-save ang PDF?",
        "My doctor said to take vitamin D, how much is safe?", "Sinabi ng doktor ko na uminom ng tubig",
        "How do I put a SIM card in my phone?", "Where should I put the router?", "Why is my wifi slow?",
        "How do I pin my location?",
    ]

    def test_P5_personal_detected(self):
        for t in self.LOCKED_POSITIVES:
            with self.subTest(t=t):
                self.assertEqual(kind(t), PERSONAL)

    def test_P8b_general(self):
        for t in self.NEGATIVES:
            with self.subTest(t=t):
                self.assertEqual(kind(t), CANDIDATE)

    def test_P8c_howwhy_secret_goes_implicit(self):
        c = classify(normalize("How do I change my wifi password?").text)
        self.assertEqual(c.kind, CANDIDATE)
        self.assertTrue(c.implicit_from_b)


class LocalCommands(unittest.TestCase):
    def test_commands(self):
        for t, cmd in [("lock the vault", "lock"), ("Lock po ang vault", "lock"), ("Paki-lock ang vault", "lock"),
                       ("Anong oras na po?", "time"), ("status", "status"), ("What's the time?", "time")]:
            with self.subTest(t=t):
                c = classify(normalize(t).text)
                self.assertEqual((c.kind, c.command), (LOCAL_COMMAND, cmd))

    def test_P19_not_commands(self):
        self.assertEqual(kind("What time is it in Tokyo?"), CANDIDATE)
        self.assertNotEqual(kind("Paano i-lock ang GCash app ko?"), LOCAL_COMMAND)
        self.assertEqual(kind("What's the status of my visa in my notes?"), PERSONAL)

    def test_lock_hint(self):
        self.assertEqual(kind("Can you please make sure the vault is lock-ready"), LOCK_HINT)


class Routing(unittest.TestCase):
    def run_route(self, text, vault_ready=True, hits=(), partial=False, followup=None, now=100.0):
        n = normalize(text)
        calls = []

        def search(q, floor):
            calls.append((q, floor))
            return list(hits)
        r = route(n.text, n.search, vault_ready, search, lambda: partial, followup, now)
        return r, calls

    def test_P5_locked_reply_no_search(self):
        r, calls = self.run_route("What did I write about the landlord?", vault_ready=False)
        self.assertEqual(r.route, VAULT_LOCKED)
        self.assertEqual(calls, [])

    def test_P6_not_in_notes(self):
        r, _ = self.run_route("What is my neighbor's Wi-Fi password?")
        self.assertEqual(r.route, NOT_IN_NOTES)

    def test_P6b_still_reading(self):
        r, _ = self.run_route("What is my neighbor's Wi-Fi password?", partial=True)
        self.assertEqual(r.route, STILL_READING)

    def test_personal_hit(self):
        r, calls = self.run_route("What did I write about the landlord?", hits=[7])
        self.assertEqual((r.route, r.fallback_eligible, calls[0][1]), (BRAIN_RAG, False, "cos_floor"))

    def test_implicit_hit_and_money_without_marker(self):
        r, calls = self.run_route("When is the dentist appointment?", hits=[3])
        self.assertEqual((r.route, r.fallback_eligible, calls[0][1]), (BRAIN_RAG, True, "cos_floor_strict"))
        r, _ = self.run_route("Magkano ang upa?", hits=[4])
        self.assertEqual(r.route, FINANCE_DEFERRED)
        r, _ = self.run_route("Magkano ang upa?", hits=[])
        self.assertEqual(r.route, GENERAL_CHAT)

    def test_P10_sticky_brain_followup(self):
        fu = FollowUp(route=BRAIN_RAG, question="what did i write about rent", chunk_ids=[1], last_reply=90.0)
        r, calls = self.run_route("What about last year?", followup=fu, now=100.0)
        self.assertEqual(r.route, BRAIN_RAG)
        self.assertIn("what did i write about rent", calls[0][0])

    def test_P10b_sticky_never_overrides_personal(self):
        fu = FollowUp(route=GENERAL_CHAT, last_reply=90.0)
        r, _ = self.run_route("and what did I write about the vet?", vault_ready=False, followup=fu, now=100.0)
        self.assertEqual(r.route, VAULT_LOCKED)

    def test_P10c_bare_and_not_sticky(self):
        fu = FollowUp(route=FINANCE_DEFERRED, last_reply=90.0)
        r, _ = self.run_route("And who won the 2022 World Cup?", vault_ready=False, followup=fu, now=100.0)
        self.assertEqual(r.route, GENERAL_CHAT)

    def test_sticky_expires_after_90s(self):
        fu = FollowUp(route=FINANCE_DEFERRED, last_reply=0.0)
        r, _ = self.run_route("What about last week?", vault_ready=False, followup=fu, now=100.0)
        self.assertEqual(r.route, GENERAL_CHAT)


class Tts(unittest.TestCase):
    VECTORS = {
        "₱1.00": "1 peso", "₱0.01": "1 centavo", "₱0.50": "50 centavos", "₱12,450.00": "12,450 pesos",
        "₱12,450.75": "12,450 pesos and 75 centavos", "-₱500": "minus 500 pesos", "PHP 1,000": "1,000 pesos",
        "Php500": "500 pesos", "pay P500 now": "pay 500 pesos now", "(P500)": "(500 pesos)",
        "₱1.5M": "1.5 million pesos", "P2P": "P2P", "P1 test": "P1 test", "P1a": "P1a",
        "2026-11-24": "November 24, 2026", "2026/11/24": "November 24, 2026", "**bold** text": "bold text",
        "# Head": "Head.", "C# and #1": "C# and #1", "2 * 3 * 4": "2 * 3 * 4", "my_file_name": "my_file_name",
        "PHP 8.3 added enums": "PHP 8.3 added enums", "| a | b |\n|---|---|\n| 1 | 2 |": "a, b\n1, 2",
        "Gladeon uses GCash and BPI": "Glad-eon uses G-Cash and B P I",
    }

    def test_P12_vectors(self):
        for src, want in self.VECTORS.items():
            with self.subTest(src=src):
                self.assertEqual(to_tts(src), want)

    def test_paragraph_break_kept(self):
        self.assertEqual(to_tts("a\n\nb"), "a\n\nb")

    def test_code_fence_and_length(self):
        self.assertEqual(to_tts("```\nx=1\n```"), "Code omitted.")
        long = ("This is a sentence. " * 60).strip()
        out = to_tts(long)
        self.assertTrue(out.endswith("The rest is on your screen.") and len(out) < 960)


class Replies(unittest.TestCase):
    def test_still_reading_zero_is_cant_search(self):
        self.assertEqual(replies.still_reading(0), replies.CANT_SEARCH)
        self.assertIn("(3 left)", replies.still_reading(3)[0])


if __name__ == "__main__":
    unittest.main()
