import React from 'react'
import { Modal, TouchableOpacity, ScrollView, Alert } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { Contact, ConsentState } from '../../types/crm'

export interface ImportExportSheetProps {
  visible: boolean
  onClose: () => void
  onApplyImport?: (items: Contact[]) => void
  testID?: string
}

const allowedFields = ['name', 'email', 'phone', 'tags', 'consent', 'vip', 'channels'] as const

const sampleCsv = {
  columns: ['Full Name', 'Email Address', 'Phone', 'Tags'],
  rows: [
    ['Sarah Johnson', 'sarah@example.com', '+1-555-123-4567', 'Enterprise;VIP'],
    ['Michael Chen', 'mchen@startup.io', '', 'Startup;Technical'],
    ['Emma Rodriguez', '', '+1-555-222-3344', 'Billing'],
  ],
}

const ImportExportSheet: React.FC<ImportExportSheetProps> = ({ visible, onClose, onApplyImport, testID }) => {
  const [tab, setTab] = React.useState<'import' | 'export'>('import')
  const [mapping, setMapping] = React.useState<Record<string, typeof allowedFields[number]>>({
    'Full Name': 'name',
    'Email Address': 'email',
    'Phone': 'phone',
    'Tags': 'tags',
  })
  const [exportFields, setExportFields] = React.useState<Record<typeof allowedFields[number], boolean>>({
    name: true,
    email: true,
    phone: true,
    tags: true,
    consent: true,
    vip: true,
    channels: true,
  })

  const cycleField = (col: string) => {
    const current = mapping[col]
    const idx = allowedFields.indexOf(current)
    const next = allowedFields[(idx + 1) % allowedFields.length]
    setMapping((m) => ({ ...m, [col]: next }))
  }

  const applyImport = () => {
    // Convert sampleCsv with mapping → Contact[] (minimal fields)
    const items: Contact[] = sampleCsv.rows.map((row, rIdx) => {
      const asRecord: Record<string, string> = {}
      sampleCsv.columns.forEach((col, cIdx) => {
        asRecord[col] = row[cIdx] || ''
      })
      const name = asRecord[sampleCsv.columns[0]] || `Imported ${rIdx + 1}`
      const email = mapping['Email Address'] ? asRecord['Email Address'] : undefined
      const phone = mapping['Phone'] ? asRecord['Phone'] : undefined
      const tags = (asRecord['Tags'] || '').split(/[;,]/).map((t) => t.trim()).filter(Boolean)
      const consent: ConsentState = 'granted'
      return {
        id: `imp-${Date.now()}-${rIdx}`,
        name,
        email,
        phone,
        channels: ['web'],
        tags,
        vip: tags.includes('VIP'),
        lastInteractionTs: Date.now() - 1000 * 60 * (rIdx + 1),
        lifetimeValue: undefined,
        consent,
      }
    })
    onApplyImport?.(items)
    onClose()
  }

  const toggleExportField = (k: typeof allowedFields[number]) => {
    setExportFields((f) => ({ ...f, [k]: !f[k] }))
  }

  const runExport = () => {
    // Stub: pretend to generate CSV and show toast/alert
    const picked = Object.entries(exportFields).filter(([, v]) => v).map(([k]) => k)
    console.log('Export fields', picked)
    Alert.alert('Export ready', `Fields: ${picked.join(', ')}`)
    onClose()
  }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Box style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
        <Box p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderTopLeftRadius: 16, borderTopRightRadius: 16 }} testID={testID}>
          {/* Header */}
          <Box row align="center" justify="space-between" mb={12}>
            <Box row align="center" gap={8}>
              {(['import', 'export'] as const).map((t) => (
                <TouchableOpacity key={t} onPress={() => setTab(t)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tab === t ? tokens.colors.primary : tokens.colors.border }}>
                  <Text size={12} weight="600" color={tab === t ? tokens.colors.primary : tokens.colors.mutedForeground}>{t.toUpperCase()}</Text>
                </TouchableOpacity>
              ))}
            </Box>
            <TouchableOpacity onPress={onClose} accessibilityLabel="Close" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
              <Text size={14} color={tokens.colors.mutedForeground}>Close</Text>
            </TouchableOpacity>
          </Box>

          {/* Body */}
          {tab === 'import' ? (
            <ScrollView style={{ maxHeight: 460 }}>
              {/* File picker stub */}
              <TouchableOpacity onPress={() => { /* pick file */ }} style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border, marginBottom: 12 }}>
                <Text style={{ color: tokens.colors.cardForeground }}>Pick CSV (stub)</Text>
              </TouchableOpacity>
              {/* Preview */}
              <Box mb={12}>
                <Text size={12} color={tokens.colors.mutedForeground} style={{ marginBottom: 6 }}>Preview (first 3 rows)</Text>
                <Box style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
                  {/* Header row */}
                  <Box row style={{ backgroundColor: tokens.colors.accent }}>
                    {sampleCsv.columns.map((c) => (
                      <Box key={c} style={{ flex: 1, padding: 8, borderRightWidth: 1, borderRightColor: tokens.colors.border }}>
                        <Text size={12} weight="600" color={tokens.colors.cardForeground}>{c}</Text>
                      </Box>
                    ))}
                  </Box>
                  {/* Data rows */}
                  {sampleCsv.rows.map((r, ri) => (
                    <Box key={ri} row>
                      {r.map((cell, ci) => (
                        <Box key={ci} style={{ flex: 1, padding: 8, borderRightWidth: 1, borderRightColor: tokens.colors.border }}>
                          <Text size={12} color={tokens.colors.mutedForeground}>{cell || '—'}</Text>
                        </Box>
                      ))}
                    </Box>
                  ))}
                </Box>
              </Box>
              {/* Mapping */}
              <Box>
                <Text size={12} color={tokens.colors.mutedForeground} style={{ marginBottom: 6 }}>Map columns to fields (tap to cycle)</Text>
                {sampleCsv.columns.map((c) => (
                  <Box key={c} row align="center" justify="space-between" style={{ paddingVertical: 8 }}>
                    <Text size={13} color={tokens.colors.cardForeground}>{c}</Text>
                    <TouchableOpacity onPress={() => cycleField(c)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border }}>
                      <Text style={{ color: tokens.colors.primary, fontWeight: '600' }}>{mapping[c]}</Text>
                    </TouchableOpacity>
                  </Box>
                ))}
              </Box>
              <TouchableOpacity onPress={applyImport} style={{ alignSelf: 'flex-end', marginTop: 12, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border }}>
                <Text style={{ color: tokens.colors.primary, fontWeight: '600' }}>Apply</Text>
              </TouchableOpacity>
            </ScrollView>
          ) : (
            <ScrollView style={{ maxHeight: 460 }}>
              <Text size={12} color={tokens.colors.mutedForeground} style={{ marginBottom: 8 }}>Pick fields to export</Text>
              {allowedFields.map((f) => (
                <TouchableOpacity key={f} onPress={() => toggleExportField(f)} accessibilityLabel={`Toggle ${f}`} accessibilityRole="button" style={{ paddingVertical: 8 }}>
                  <Text style={{ color: exportFields[f] ? tokens.colors.primary : tokens.colors.mutedForeground, fontWeight: '600' }}>{exportFields[f] ? '☑' : '☐'} {f}</Text>
                </TouchableOpacity>
              ))}
              <TouchableOpacity onPress={runExport} style={{ alignSelf: 'flex-end', marginTop: 12, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border }}>
                <Text style={{ color: tokens.colors.primary, fontWeight: '600' }}>Export CSV</Text>
              </TouchableOpacity>
            </ScrollView>
          )}
        </Box>
      </Box>
    </Modal>
  )
}

export default ImportExportSheet


