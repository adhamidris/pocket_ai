export const helpStrings = {
  en: {
    askAboutThis: 'Ask Assistant about this',
    generateStepByStep: 'Generate step‑by‑step',
    skip: 'Skip',
    next: 'Next',
    stepXofY: (x: number, y: number) => `Step ${x} of ${y}`,
    quickstartTitle: 'Quickstart Checklist',
    progress: (p: number) => `${p}% complete`,
    cached: 'Cached',
  },
  ar: {
    askAboutThis: 'اسأل المساعد عن هذا',
    generateStepByStep: 'إنشاء خطوات إرشادية',
    skip: 'تخطي',
    next: 'التالي',
    stepXofY: (x: number, y: number) => `الخطوة ${x} من ${y}`,
    quickstartTitle: 'قائمة البدء السريع',
    progress: (p: number) => `${p}% مكتمل`,
    cached: 'مخزن مؤقتًا',
  }
}

export type HelpLocale = keyof typeof helpStrings


