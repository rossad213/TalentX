#!/usr/bin/env python3
from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from validate_non_athlete_pricing import validate

class NonAthletePricingValidationTests(unittest.TestCase):
    def rules(self):
        return {"pairs":[{"category":"Music","higher":"Taylor Swift","lower":"Demi Lovato","minimumGapPct":0}]}
    def test_passes_correct_order(self):
        failures,notices=validate([
            {"name":"Taylor Swift","primaryCategory":"Music","marketPrice":220},
            {"name":"Demi Lovato","primaryCategory":"Music","marketPrice":175},
        ],self.rules())
        self.assertEqual(failures,[]);self.assertEqual(notices,[])
    def test_fails_inversion(self):
        failures,_=validate([
            {"name":"Taylor Swift","primaryCategory":"Music","marketPrice":170},
            {"name":"Demi Lovato","primaryCategory":"Music","marketPrice":175},
        ],self.rules())
        self.assertTrue(failures)
    def test_skips_optional_lower_when_source_did_not_return_it(self):
        failures,notices=validate([
            {"name":"Taylor Swift","primaryCategory":"Music","marketPrice":220},
        ],self.rules())
        self.assertEqual(failures,[]);self.assertTrue(notices)

if __name__=='__main__': unittest.main()
