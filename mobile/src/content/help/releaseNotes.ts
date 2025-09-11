import { ReleaseNote } from '../../types/help'

export const releaseNotes: ReleaseNote[] = [
  {
    id: '1.2.0',
    version: '1.2.0',
    dateIso: '2025-09-10',
    highlights: [
      { title: 'Tours engine', body: 'Guided tours with coachmarks and spotlights.', route: 'HelpCenter' },
      { title: 'Help Center', body: 'Centralized search, checklists, and release notes.' }
    ]
  },
  {
    id: '1.1.0',
    version: '1.1.0',
    dateIso: '2025-08-20',
    highlights: [
      { title: 'Performance', body: 'Dashboard optimized for faster loads.' },
    ]
  }
]


