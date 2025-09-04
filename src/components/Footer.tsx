import { Button } from "@/components/ui/button";
import { ArrowRight, Twitter, Linkedin, Github, Mail } from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";

const Footer = () => {
  const { t } = useI18n();
  const footerLinks = t<Record<string, string[]>>("footer.categories");
  const socialLinks = [
    { icon: Twitter, href: "#", label: t("footer.social.twitter") },
    { icon: Linkedin, href: "#", label: t("footer.social.linkedin") },
    { icon: Github, href: "#", label: t("footer.social.github") },
    { icon: Mail, href: "#", label: t("footer.social.email") },
  ];

  return (
    <footer className="bg-background border-t border-border">
      {/* Main Footer Content */}
      <div className="container mx-auto px-6 py-16">
        <div className="grid lg:grid-cols-6 gap-8">
          {/* Company Info */}
          <div className="lg:col-span-2 md:text-left text-center">
            <div className="flex items-center gap-2 mb-6 justify-center md:justify-start">
              <div className="w-10 h-10 bg-gradient-primary rounded-xl flex items-center justify-center">
                <span className="text-white font-bold text-xl">A</span>
              </div>
              <span className="text-2xl font-bold text-foreground">{t("footer.brand")}</span>
            </div>
            <p className="text-muted-foreground mb-6 leading-relaxed md:text-left text-center">
              {t("footer.blurb")}
            </p>
            
            {/* Social Links */}
            <div className="flex gap-4 justify-center md:justify-start">
              {socialLinks.map((social) => (
                <a
                  key={social.label}
                  href={social.href}
                  className="w-10 h-10 bg-muted rounded-lg flex items-center justify-center hover:bg-accent transition-colors group"
                  aria-label={social.label}
                >
                  <social.icon className="w-5 h-5 text-muted-foreground group-hover:text-foreground transition-colors" />
                </a>
              ))}
            </div>
          </div>

          {/* Footer Links */}
          {Object.entries(footerLinks).filter(([category]) => category !== 'Resources').map(([category, links]) => (
            <div key={category} className="md:text-left text-center">
              <h4 className="font-semibold text-foreground mb-4">{category}</h4>
              <ul className="space-y-3">
                {links.map((link) => (
                  <li key={link}>
                    <a
                      href="#"
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {link}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        {/* Bottom Bar */}
        <div className="mt-16 pt-8 border-t border-border flex justify-center items-center">
          <div className="text-muted-foreground text-sm text-center">
            {t("footer.bottom.copyright")}
          </div>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
