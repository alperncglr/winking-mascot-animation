from __future__ import annotations

import unittest

from meeting_scribe.domain.models import SummaryResult
from meeting_scribe.services.summarize import _summary_prompt, result_from_json
from meeting_scribe.storage.db import Database
from meeting_scribe.storage.repositories import MeetingRepository, SummaryRepository
from tests.helpers import workspace_tempdir


class SummaryTests(unittest.TestCase):
    def test_result_uses_selected_sections_and_normalizes_actions(self) -> None:
        result = result_from_json(
            {
                'short_summary': 'Proje takvimi değerlendirildi.',
                'discussed_topics': ['Takvim', 'Kaynak planı'],
                'decisions': ['Canlıya çıkış cuma günü yapılacak.'],
                'actions': [
                    {
                        'task': 'Test raporunu tamamla',
                        'owner': 'Ayşe',
                    },
                    'Dağıtım notlarını hazırla',
                ],
                'open_questions': ['Ek sunucu gerekli mi?'],
            }
        )

        self.assertEqual(
            result.actions,
            [
                {
                    'task': 'Test raporunu tamamla',
                    'owner': 'Ayşe',
                    'deadline': 'Belirtilmedi',
                },
                {
                    'task': 'Dağıtım notlarını hazırla',
                    'owner': 'Belirtilmedi',
                    'deadline': 'Belirtilmedi',
                },
            ],
        )
        self.assertIn('# Kısa Özet', result.summary_md)
        self.assertIn('## Görüşülen Konular', result.summary_md)
        self.assertIn('## Alınan Kararlar', result.summary_md)
        self.assertIn('## Aksiyonlar', result.summary_md)
        self.assertIn('## Açık Sorular', result.summary_md)
        self.assertNotIn('Sonraki Toplantıya Devredenler', result.summary_md)
        self.assertIn('  - Sorumlu: Ayşe', result.summary_md)
        self.assertIn('  - Termin: Belirtilmedi', result.summary_md)

    def test_prompt_forbids_inventing_owner_or_deadline(self) -> None:
        prompt = _summary_prompt('örnek transkript')
        self.assertIn('"discussed_topics"', prompt)
        self.assertIn('"open_questions"', prompt)
        self.assertIn("kesinlikle uydurma", prompt)
        self.assertIn("'Belirtilmedi'", prompt)

    def test_repository_migrates_and_round_trips_new_summary_fields(self) -> None:
        with workspace_tempdir() as tmp:
            db = Database(tmp / 'summary.sqlite3')
            db.initialize()
            meeting_id = MeetingRepository(db).create_meeting(
                'Özet testi', 'Operator', 'operator@example.local'
            )
            summaries = SummaryRepository(db)
            expected = SummaryResult(
                summary_md='# Kısa Özet\n\nTest\n',
                discussed_topics=['Konu'],
                decisions=['Karar'],
                actions=[
                    {
                        'task': 'Görev',
                        'owner': 'Sorumlu',
                        'deadline': 'Yarın',
                    }
                ],
                open_questions=['Soru?'],
            )

            summaries.upsert(meeting_id, expected)
            stored = summaries.get(meeting_id)

            self.assertIsNotNone(stored)
            self.assertEqual(stored['discussed_topics'], ['Konu'])
            self.assertEqual(stored['decisions'], ['Karar'])
            self.assertEqual(stored['actions'], expected.actions)
            self.assertEqual(stored['open_questions'], ['Soru?'])


if __name__ == '__main__':
    unittest.main()
