(function (global) {
  "use strict";

  var LOCAL_MESSAGES = {
    ar: {
      "Unable to load chat": "تعذر تحميل الدردشة",
      "Please refresh and try again.": "يرجى تحديث الصفحة والمحاولة مرة أخرى.",
      "Phone call": "مكالمة هاتفية",
      "Outgoing call": "مكالمة صادرة",
      "Toggle call details": "تبديل تفاصيل المكالمة",
      "Accept call request": "قبول طلب المكالمة",
      Accept: "قبول",
      "Reject call request": "رفض طلب المكالمة",
      Reject: "رفض",
      Rejected: "مرفوض",
      "Approval Expired": "انتهت صلاحية الموافقة",
      "Calling…": "جارٍ الاتصال…",
      Approved: "تمت الموافقة",
      Queued: "قيد الانتظار",
      Throttled: "تم التقييد",
      Failed: "فشل",
      To: "إلى",
      Contact: "جهة الاتصال",
      Objective: "الهدف",
      Type: "النوع",
      Language: "اللغة",
      "Max duration": "المدة القصوى",
      Context: "السياق",
      "Hide details": "إخفاء التفاصيل",
      "Show details": "إظهار التفاصيل",
      "Sending email...": "جارٍ إرسال البريد...",
      "Creating draft...": "جارٍ إنشاء المسودة...",
      "Hide email": "إخفاء البريد",
      CC: "نسخة",
      Subject: "الموضوع",
      Message: "الرسالة",
      "Show email": "إظهار البريد",
      "Email details": "تفاصيل البريد",
      "Draft created": "تم إنشاء المسودة",
      "Not sent": "لم يتم الإرسال",
      "Approval expired": "انتهت صلاحية الموافقة",
      Customer: "العميل",
      Agent: "الوكيل",
      "Active Voice Call": "مكالمة صوتية نشطة",
      Live: "مباشر",
      "Real-time transcript": "تفريغ لحظي",
      Transcript: "التفريغ",
      "Waiting for speech...": "بانتظار التحدث...",
      Ringing: "يرن",
      Dialing: "جارٍ الاتصال",
      Done: "تم",
      Cancelled: "تم الإلغاء",
      "Call cancelled.": "تم إلغاء المكالمة.",
      "Call failed.": "فشلت المكالمة.",
      "Call completed.": "اكتملت المكالمة.",
      "Ringing…": "يرن…",
      "Dialing…": "جارٍ الاتصال…",
      "Waiting for the call to start…": "بانتظار بدء المكالمة…",
      "Waiting for speech…": "بانتظار التحدث…",
      Call: "مكالمة",
      Country: "الدولة",
      Details: "التفاصيل",
      "Background task": "مهمة في الخلفية",
      "Agentic Task": "مهمة وكيلية",
      Scheduled: "مجدولة",
      "Manual only": "يدوية فقط",
      Approval: "الموافقة",
      "Waiting for approval.": "بانتظار الموافقة.",
      "This task needs your approval to continue.": "تحتاج هذه المهمة إلى موافقتك للمتابعة.",
      Approve: "موافقة",
      Deny: "رفض",
      Question: "سؤال",
      "Type your answer…": "اكتب إجابتك…",
      Send: "إرسال",
      Waiting: "بانتظار",
      "This task is waiting on another agent. Check the Inbox for updates.":
        "هذه المهمة بانتظار وكيل آخر. تحقق من صندوق الوارد للحصول على التحديثات.",
      "Open inbox": "فتح صندوق الوارد",
      "Needs approval": "تحتاج إلى موافقة",
      "Needs your input": "تحتاج إلى مدخلاتك",
      Completed: "مكتملة",
      Error: "خطأ",
      "Waiting for updates…": "بانتظار التحديثات…",
      Step: "خطوة",
      "No plan available yet.": "لا توجد خطة متاحة بعد.",
      Plan: "الخطة",
      Action: "الإجراء",
      Checkpoint: "نقطة التحقق",
      Payload: "البيانات",
      "Checkpoint resolved": "تم حل نقطة التحقق",
      "No reply text was provided.": "لم يتم تقديم نص للرد.",
      "Run unavailable": "التشغيل غير متاح",
      "Run queued": "تمت إضافة التشغيل إلى الصف",
      "Run task now": "شغّل المهمة الآن",
      "Agentic Task run endpoint is not configured.": "نقطة تشغيل المهمة الوكيلية غير مهيأة.",
      "Agentic Task run failed.": "فشل تشغيل المهمة الوكيلية.",
      "Agentic Task run started.": "بدأ تشغيل المهمة الوكيلية.",
      "Session token missing.": "رمز الجلسة مفقود.",
      "Custom Assistants open as chat sessions.": "تُفتح المساعدات المخصصة كجلسات دردشة.",
      "Open the assistant from the sidebar instead.": "افتح المساعد من الشريط الجانبي بدلاً من ذلك.",
    },
  };

  function normalizedLanguageCode() {
    var code = "";
    try {
      if (global.document && global.document.documentElement) {
        code = asText(global.document.documentElement.getAttribute("lang"));
      }
    } catch (_err) {
      code = "";
    }
    code = code.toLowerCase();
    if (code.indexOf("ar") === 0) {
      return "ar";
    }
    return "en";
  }

  function asText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value);
  }

  function interpolate(template, params) {
    var text = asText(template);
    if (!params || typeof params !== "object") {
      return text;
    }
    return text.replace(/%\(([^)]+)\)s/g, function (_match, key) {
      if (!Object.prototype.hasOwnProperty.call(params, key)) {
        return "";
      }
      return asText(params[key]);
    });
  }

  function translate(message, params) {
    var source = asText(message);
    if (!source) {
      return "";
    }
    var output = source;
    if (typeof global.gettext === "function") {
      try {
        output = global.gettext(source);
      } catch (_err) {
        output = source;
      }
    }
    var lang = normalizedLanguageCode();
    var localCatalog = LOCAL_MESSAGES[lang] || {};
    if ((!output || output === source) && Object.prototype.hasOwnProperty.call(localCatalog, source)) {
      output = localCatalog[source];
    }
    return interpolate(output, params);
  }

  function translatePlural(singular, plural, count, params) {
    var one = asText(singular);
    var many = asText(plural);
    var translated = count === 1 ? one : many;
    if (typeof global.ngettext === "function") {
      try {
        translated = global.ngettext(one, many, Number(count) || 0);
      } catch (_err) {
        translated = count === 1 ? one : many;
      }
    }
    var lang = normalizedLanguageCode();
    var localCatalog = LOCAL_MESSAGES[lang] || {};
    if ((!translated || translated === (count === 1 ? one : many)) && count === 1 && Object.prototype.hasOwnProperty.call(localCatalog, one)) {
      translated = localCatalog[one];
    }
    var merged = Object.assign({ count: count }, params || {});
    return interpolate(translated, merged);
  }

  global.PocketI18n = Object.assign({}, global.PocketI18n || {}, {
    t: translate,
    n: translatePlural,
    interpolate: interpolate,
  });
})(window);
