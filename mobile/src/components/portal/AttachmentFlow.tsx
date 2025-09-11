import { MsgPartFile } from '../../types/portal'

const ALLOWED: string[] = [
  'image/png',
  'image/jpeg',
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
]

const MAX_KB = 10 * 1024 // 10MB

export const validateFile = (f: MsgPartFile): { ok: true } | { ok: false; error: string } => {
  if (f.sizeKB > MAX_KB) return { ok: false, error: 'File exceeds 10MB size limit' }
  if (!ALLOWED.includes(f.mime)) return { ok: false, error: 'Unsupported file type' }
  return { ok: true }
}

// UI-only fake picker — returns one random file
export const pickFakeFile = (): MsgPartFile => {
  const samples: MsgPartFile[] = [
    { kind: 'file', name: `photo_${Date.now()}.png`, sizeKB: 320, mime: 'image/png' },
    { kind: 'file', name: `scan_${Date.now()}.jpg`, sizeKB: 480, mime: 'image/jpeg' },
    { kind: 'file', name: `doc_${Date.now()}.pdf`, sizeKB: 900, mime: 'application/pdf' },
  ]
  return samples[Math.floor(Math.random() * samples.length)]
}


