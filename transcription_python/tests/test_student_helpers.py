import unittest

from app.student_helpers import (
    normalize_student_name,
    parse_student_date,
    student_matricule,
)


class StudentHelperTests(unittest.TestCase):
    def test_normalizes_accented_name(self):
        self.assertEqual(normalize_student_name("Éboué"), "EBOUE")

    def test_generates_compatible_matricule(self):
        self.assertEqual(
            student_matricule("Kouadio", "Alain", "1998-03-17"),
            "KOUA1703980001",
        )

    def test_accepts_supported_date_formats(self):
        self.assertEqual(parse_student_date("17/03/1998"), "1998-03-17")
        self.assertEqual(parse_student_date("17-03-1998"), "1998-03-17")

    def test_rejects_invalid_or_future_dates(self):
        self.assertIsNone(parse_student_date("31/02/1998"))
        self.assertIsNone(parse_student_date("2999-01-01"))


if __name__ == "__main__":
    unittest.main()
