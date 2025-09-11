export interface PortalCopy {
  welcome: string
  reconnecting: string
  consentText: string
  prechatEnabled: string
  consentRequired: string
  profanityNotice: string
  cooldown: (secs: number) => string
  transcriptSent: (email: string) => string
  queueRequested: string
  queueCancelled: string
}

const copies: Record<string, PortalCopy> = {
  en: {
    welcome: 'Welcome to Acme Support!',
    reconnecting: 'Reconnecting…',
    consentText: 'By starting chat, you agree to our privacy policy.',
    prechatEnabled: 'Pre‑chat form is enabled (UI‑only).',
    consentRequired: 'Consent is required before messaging (UI‑only).',
    profanityNotice: 'Please avoid profanity.',
    cooldown: (s) => `Please wait ${s}s before sending more messages.`,
    transcriptSent: (email) => `Transcript sent to ${email}`,
    queueRequested: 'Human agent requested. You are now in queue.',
    queueCancelled: 'Cancelled. You are chatting with AI.'
  },
  ar: {
    welcome: 'مرحبًا بكم في دعم أكمي!',
    reconnecting: 'إعادة الاتصال…',
    consentText: 'بدء الدردشة يعني الموافقة على سياسة الخصوصية.',
    prechatEnabled: 'نموذج ما قبل الدردشة مفعّل (واجهة فقط).',
    consentRequired: 'يتطلب المتابعة الموافقة (واجهة فقط).',
    profanityNotice: 'يرجى تجنب الألفاظ غير اللائقة.',
    cooldown: (s) => `يرجى الانتظار ${s} ث قبل الإرسال.`,
    transcriptSent: (email) => `تم إرسال المحادثة إلى ${email}`,
    queueRequested: 'تم طلب وكيل بشري. أنت الآن في قائمة الانتظار.',
    queueCancelled: 'تم الإلغاء. أنت تتحدث مع الذكاء الاصطناعي.'
  }
}

const normalize = (locale?: string): keyof typeof copies => {
  const l = (locale || 'en').toLowerCase()
  if (l.startsWith('ar')) return 'ar'
  return 'en'
}

export const getPortalCopy = (locale?: string): PortalCopy => copies[normalize(locale)]


