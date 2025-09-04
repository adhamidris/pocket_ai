import { useI18n } from "@/i18n/I18nProvider";

const LanguageToggle = () => {
  const { lang, toggleLang, t } = useI18n();
  const other = lang === "en" ? "ar" : "en";
  return (
    <button
      onClick={toggleLang}
      className="px-2.5 py-1.5 text-sm rounded-lg border border-border hover:bg-accent/40 transition-colors"
      aria-label={t("nav.toggleLanguage")}
      title={t("nav.toggleLanguage")}
    >
      <span className="font-medium">{t(`nav.language.${lang}`)}</span>
      <span className="mx-1 text-neutral-400">/</span>
      <span className="text-neutral-500">{t(`nav.language.${other}`)}</span>
    </button>
  );
};

export default LanguageToggle;

