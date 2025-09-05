export default {
  home: {
    ctaStart: 'ابدأ الآن',
    ctaSignIn: 'تسجيل الدخول'
  },
  hero: {
    title: 'الأمر بسيط!',
    subtitle: 'قدّم دعماً فورياً وذكياً على مدار الساعة بمنصتنا المدعومة بالذكاء الاصطناعي.'
  },
  howItWorks: {
    steps: [
      { title: 'سجّل', bullets: ['أكمل ملف العمل', 'ارفع SOP/قاعدة المعرفة', 'أو دَع الذكاء يولّدها لك'] },
      { title: 'أنشئ وكيلك الذكي', bullets: ['اضبط الشخصية والأدوات', 'احصل على رابط البوابة'] },
      { title: 'الصق! الصق! الصق!', bullets: ['انشر الرابط على قنواتك', 'واسترخِ وتابع'] },
    ]
  },
  featuresSnap: {
    title: 'أهم الميزات',
    items: [
      'مساعد ذكي',
      'استجابات سريعة جدًا',
      'أمان على مستوى المؤسسات',
      'تحليلات متقدمة'
    ]
  },
  testimonials: {
    title: 'ماذا يقول العملاء',
    items: [
      { quote: 'انخفضت أوقات الاستجابة من دقائق إلى ثوانٍ. ارتفع الرضا 25%.', author: 'Maya Patel', role: 'مديرة الدعم، Flowify' },
      { quote: 'أتممنا التحديثات والاسترجاعات خلال أيام. التذاكر أقل والإيرادات أعلى.', author: 'Alex Romero', role: 'المدير التنفيذي للعمليات، CloudCart' },
      { quote: 'الإعداد كان سريعًا جدًا. الدردشة تبدو أصلية لعلامتنا.', author: 'Sofia Nguyen', role: 'نائب رئيس التسويق، BrightClinic' },
      { quote: 'حللت التحليلات أنماطًا لم نرها من قبل.', author: 'Daniel Kim', role: 'قائد العمليات، Loop' }
    ]
  },
  pricing: {
    title: 'أسعار بسيطة',
    perMonth: '/شهر',
    getStarted: 'ابدأ الآن',
    seeFull: 'عرض الأسعار الكاملة',
    billing: { monthly: 'شهري', yearly: 'سنوي', yearlyNote: 'تُحسب سنوياً' },
    packages: [
      { tier: 'Plus', monthly: 29, yearly: 24, features: ['وكيل واحد', 'بوابة دردشة بعلامتك', 'قاعدة معرفة أساسية'] },
      { tier: 'Pro', monthly: 89, yearly: 69, features: ['حتى 3 وكلاء', 'قاعدة معرفة متقدمة + استشهادات', 'سير عمل وأدوات'] },
      { tier: 'Enterprise', monthly: 249, yearly: 199, features: ['وكلاء بلا حد', 'SSO وسجلات تدقيق', 'توجيه مخصص'] }
    ]
  },
  demo: {
    scenarios: [
      {
        agentName: 'نانسي', jobTitle: 'وكيل دعم التجارة الإلكترونية',
        conversation: [
          { id: 1, text: 'مرحباً! أحتاج مساعدة بخصوص طلبي 12345', isBot: false, delay: 1000 },
          { id: 2, text: 'مرحباً، أنا نانسي. يسعدني مساعدتك. سأتحقق من الطلب الآن.', isBot: true, delay: 1400 },
          { id: 3, text: 'شكراً لانتظارك. تم شحن طلبك أمس وسيصل غداً بحلول الثالثة مساءً.', isBot: true, delay: 1600 }
        ]
      }
    ]
  }
}
