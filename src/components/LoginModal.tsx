import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/i18n/I18nProvider";
import { Link } from "react-router-dom";

export const LoginModal = ({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) => {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md login-modal">
        <DialogHeader>
          <DialogTitle className="text-2xl font-bold text-center">
            {t("auth.login.titlePrefix")} {" "}
            <span className="text-gradient-hero">Pocket</span>
          </DialogTitle>
          <DialogDescription className="text-center streaming-text">{t("auth.login.subtitle")}</DialogDescription>
        </DialogHeader>
        <div className="mt-2 space-y-3">
          <div>
            <label className="block text-sm font-medium mb-2">{t("auth.login.email")}</label>
            <Input
              type="email"
              placeholder={t("auth.register.placeholders.email")}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="login-input bg-input border border-input"
              autoComplete="email"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">{t("auth.login.password")}</label>
            <Input
              type="password"
              placeholder={t("auth.register.placeholders.password")}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="login-input bg-input border border-input"
              autoComplete="current-password"
            />
          </div>
          <Button className="w-full bg-gradient-primary text-white hover:opacity-90">{t("auth.login.loginBtn")}</Button>

          {/* Divider */}
          <div className="my-1">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs text-muted-foreground">{t("auth.login.or")}</span>
              <div className="h-px flex-1 bg-border" />
            </div>
          </div>

          {/* Google */}
          <Button type="button" variant="secondary" className="w-full">
            <span className="inline-flex items-center gap-2">
              <span className="w-6 h-6 rounded-full inline-flex items-center justify-center">
                <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden>
                  <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36 16.8 36 11 30.2 11 23S16.8 10 24 10c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 12 2 2 12 2 24s10 22 22 22 22-10 22-22c0-1.3-.1-2.5-.4-3.5z"/>
                  <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.4 16.3 18.8 14 24 14c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 15.3 2 7.8 7.1 4.2 14.1l2.1.6z"/>
                  <path fill="#4CAF50" d="M24 46c6 0 11.5-2.2 15.6-5.8l-7.2-5.9C30.7 35.7 27.6 37 24 37c-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46z"/>
                  <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-1.3 3.8-4.8 6.5-9.3 6.5-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46c12 0 22-10 22-22 0-1.3-.1-2.5-.4-3.5z"/>
                </svg>
              </span>
              <span>{t("auth.register.continueGoogle")}</span>
            </span>
          </Button>

          <div className="text-center text-sm text-muted-foreground">
            {t("auth.login.noAccount")} {" "}
            <Link to="/register" className="text-primary hover:underline" onClick={() => onOpenChange(false)}>
              {t("auth.login.signUp")}
            </Link>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default LoginModal;
