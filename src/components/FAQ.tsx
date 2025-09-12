import { useI18n } from "@/i18n/I18nProvider";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";

const FAQ = () => {
  const { t } = useI18n();
  const items = t<{ q: string; a: string }[]>("faq.items");
  return (
    <section id="faq" className="bg-background pt-16 md:pt-20 pb-20 md:pb-24">
      <div className="container mx-auto px-6">
        <div className="text-center mb-12 md:mb-16 animate-fade-in">
          <h2 className="text-4xl md:text-5xl font-semibold text-foreground">
            {t("faq.titlePrefix")} {" "}
            <span className="text-gradient-hero">{t("faq.titleHighlight")}</span>
          </h2>
        </div>
        <div className="mx-auto max-w-4xl bg-card border border-border rounded-2xl p-5 md:p-7 shadow-premium animate-panel-in">
          <Accordion type="single" collapsible className="w-full">
            {items.map((it, idx) => (
              <AccordionItem key={idx} value={`item-${idx}`} className="border-border">
                <AccordionTrigger className="text-start text-card-foreground text-lg md:text-xl">
                  {it.q}
                </AccordionTrigger>
                <AccordionContent className="text-muted-foreground text-base leading-relaxed animate-fade-in">
                  {it.a}
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      </div>
    </section>
  );
};

export default FAQ;

