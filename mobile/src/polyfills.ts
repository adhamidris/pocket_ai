// Ensure globalThis exists for older environments
;(function(){
  if (typeof globalThis === 'object') return
  // @ts-ignore
  Object.defineProperty(Object.prototype, '__magic__', {
    get: function() { return this }
  })
  // @ts-ignore
  // eslint-disable-next-line no-undef
  ;(__magic__ as any).globalThis = (__magic__ as any)
  // @ts-ignore
  delete (Object.prototype as any).__magic__
})()

// noop polyfills for optional APIs that some libs expect
// @ts-ignore
if (typeof (globalThis as any).requestIdleCallback !== 'function') {
  ;(globalThis as any).requestIdleCallback = (cb: any) => setTimeout(() => cb(Date.now()), 1)
}
// Intl polyfills for i18n pluralization on environments missing Intl.PluralRules
// Load minimal locale data used by the app (en, ar)
// Load Intl.PluralRules polyfill only if missing to avoid conflicts with Hermes
try {
  // @ts-ignore
  const hasIntl = typeof Intl !== 'undefined'
  // @ts-ignore
  const hasPluralRules = hasIntl && typeof (Intl as any).PluralRules !== 'undefined'
  if (!hasIntl || !hasPluralRules) {
    require('@formatjs/intl-pluralrules/polyfill')
    require('@formatjs/intl-pluralrules/locale-data/en')
    require('@formatjs/intl-pluralrules/locale-data/ar')
  } else {
    const ensureLocale = (locale: string) => {
      try {
        // eslint-disable-next-line no-new
        new (Intl as any).PluralRules(locale)
      } catch {
        if (locale.startsWith('ar')) {
          require('@formatjs/intl-pluralrules/locale-data/ar')
        } else if (locale.startsWith('en')) {
          require('@formatjs/intl-pluralrules/locale-data/en')
        }
      }
    }
    ensureLocale('en')
    ensureLocale('ar')
  }
} catch {
  // ignore: i18next will fallback to compatibility mode if Intl is not available
}
