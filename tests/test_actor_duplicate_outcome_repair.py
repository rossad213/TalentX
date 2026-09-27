from __future__ import annotations
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
from repair_actor_duplicate_outcomes import REPAIR_VERSION, repair_actor_record

class ActorDuplicateOutcomeRepairTests(unittest.TestCase):
    def test_duplicate_event_is_removed_and_extra_move_is_reversed(self):
        record={
            "id":"zendaya","primaryCategory":"Actor","marketPrice":104.04,
            "priceEvents":[
                {"eventKey":"release:1","eventType":"actor-release","startedAt":"2026-08-01T00:00:00Z","movePct":2.0,"priceBefore":100.0,"priceAfter":102.0},
                {"eventKey":"attention:1","eventType":"actor-attention-outcome","startedAt":"2026-08-08T00:00:00Z","movePct":1.0,"priceBefore":102.0,"priceAfter":103.02},
                {"eventKey":"attention:1","eventType":"actor-attention-outcome","startedAt":"2026-08-08T00:00:00Z","movePct":1.0,"priceBefore":103.02,"priceAfter":104.04},
            ],
            "priceHistory":[
                {"time":"2026-08-08T00:00:00Z","eventId":"attention:1","phase":"close","price":104.04},
                {"time":"2026-08-09T00:00:00Z","eventId":"current-market-price","phase":"close","eventType":"market-observation","price":104.04},
            ],
        }
        repaired,removed=repair_actor_record(record)
        self.assertEqual(removed,1)
        self.assertEqual(len(repaired["priceEvents"]),2)
        self.assertAlmostEqual(repaired["marketPrice"],103.01,places=2)
        self.assertEqual(repaired["actorOutcomeLedgerRepairVersion"],REPAIR_VERSION)
        self.assertEqual(repaired["actorDuplicateOutcomeEventsRemoved"],1)
        self.assertFalse(any(p.get("eventId")=="current-market-price" for p in repaired["priceHistory"]))

    def test_repair_is_idempotent(self):
        record={
            "id":"actor","primaryCategory":"Actor","marketPrice":101.0,
            "priceEvents":[
                {"eventKey":"a","eventType":"actor-attention-outcome","startedAt":"2026-08-08T00:00:00Z","movePct":1.0},
                {"eventKey":"a","eventType":"actor-attention-outcome","startedAt":"2026-08-08T00:00:00Z","movePct":1.0},
            ],
        }
        once,removed=repair_actor_record(record)
        twice,second=repair_actor_record(once)
        self.assertEqual(removed,1);self.assertEqual(second,0)
        self.assertEqual(once,twice)

    def test_non_actor_is_untouched(self):
        music={"id":"m","primaryCategory":"Music","marketPrice":100.0,"priceEvents":[{"eventKey":"x"},{"eventKey":"x"}]}
        repaired,removed=repair_actor_record(music)
        self.assertEqual(removed,0);self.assertEqual(repaired,music)

if __name__=="__main__":unittest.main()
