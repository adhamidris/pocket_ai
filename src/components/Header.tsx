import { useState } from "react";
import { useThemeMode } from "@/contexts/ThemeProvider";
import { Button } from "@/components/ui/button";
import { Menu, Moon, Sun, X } from "lucide-react";
import LanguageToggle from "./LanguageToggle";
import { useI18n } from "@/i18n/I18nProvider";
import LoginModal from "./LoginModal";
import { Link } from "react-router-dom";

const Header = () => {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const { isLight, toggle } = useThemeMode();
  const { t } = useI18n();
  const [loginOpen, setLoginOpen] = useState(false);

  

  return (
    <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-neutral-100">
      <div className="container mx-auto px-6 py-4">
        <div className="flex items-center justify-between">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-primary rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-lg">A</span>
            </div>
            <span className="text-xl font-semibold text-foreground">
              {t("nav.brand")}
            </span>
          </div>

          {/* Desktop Navigation */}
          <nav className="hidden md:flex items-center gap-8">
            <a href="#features" className="text-neutral-600 hover:text-primary transition-colors">
              {t("nav.features")}
            </a>
            <a href="#pricing" className="text-neutral-600 hover:text-primary transition-colors">
              {t("nav.pricing")}
            </a>
            <a href="#about" className="text-neutral-600 hover:text-primary transition-colors">
              {t("nav.about")}
            </a>
            <a href="#contact" className="text-neutral-600 hover:text-primary transition-colors">
              {t("nav.contact")}
            </a>
          </nav>

          {/* Theme toggle + Desktop CTA */}
          <div className="hidden md:flex items-center gap-4">
            <LanguageToggle />
            <button
              onClick={toggle}
              className="p-2 rounded-lg border border-border hover:bg-accent/40 transition-colors"
              aria-label={t("nav.toggleLightMode")}
              title={t("nav.toggleLightMode")}
            >
              {isLight ? <Moon className="w-5 h-5" /> : <Sun className="w-5 h-5" />}
            </button>
            <Button variant="ghost" className="text-neutral-600 hover:text-primary" onClick={() => setLoginOpen(true)}>
              {t("nav.signIn")}
            </Button>
            <Button asChild variant="ghost" className="text-neutral-600 hover:text-primary">
              <Link to="/register">{t("nav.register")}</Link>
            </Button>
            <Button className="bg-gradient-primary text-white hover:opacity-90 transition-opacity">
              {t("nav.getStarted")}
            </Button>
          </div>

          {/* Mobile Menu Button */}
          <button
            onClick={() => setIsMenuOpen(!isMenuOpen)}
            className="md:hidden p-2 rounded-lg hover:bg-neutral-100 transition-colors"
          >
            {isMenuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
        </div>

        {/* Mobile Menu */}
        {isMenuOpen && (
          <div className="md:hidden mt-4 py-4 border-t border-neutral-100 animate-fade-in">
            <nav className="flex flex-col space-y-4">
              <a href="#features" className="text-neutral-600 hover:text-primary transition-colors py-2">
                {t("nav.features")}
              </a>
              <a href="#pricing" className="text-neutral-600 hover:text-primary transition-colors py-2">
                {t("nav.pricing")}
              </a>
              <a href="#about" className="text-neutral-600 hover:text-primary transition-colors py-2">
                {t("nav.about")}
              </a>
              <a href="#contact" className="text-neutral-600 hover:text-primary transition-colors py-2">
                {t("nav.contact")}
              </a>
              <div className="flex flex-col space-y-2 pt-4">
                <LanguageToggle />
                <button
                  onClick={toggle}
                  className="justify-start p-2 rounded-lg border border-border hover:bg-accent/40 transition-colors inline-flex items-center gap-2"
                  aria-label={t("nav.toggleLightMode")}
                >
                  {isLight ? <Moon className="w-5 h-5" /> : <Sun className="w-5 h-5" />}
                  <span>{t("nav.lightMode")}</span>
                </button>
                <Button variant="ghost" className="justify-start text-neutral-600" onClick={() => setLoginOpen(true)}>
                  {t("nav.signIn")}
                </Button>
                <Button asChild variant="ghost" className="justify-start text-neutral-600">
                  <Link to="/register">{t("nav.register")}</Link>
                </Button>
                <Button className="bg-gradient-primary text-white justify-start">
                  {t("nav.getStarted")}
                </Button>
              </div>
            </nav>
          </div>
        )}
      </div>
      <LoginModal open={loginOpen} onOpenChange={setLoginOpen} />
    </header>
  );
};

export default Header;
