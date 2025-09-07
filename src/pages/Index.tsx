import Header from "@/components/Header";
import Hero from "@/components/Hero";
import Features from "@/components/Features";
import Footer from "@/components/Footer";
import ChatWidget from "@/components/ChatWidget";
import Testimonials from "@/components/Testimonials";
import Pricing from "@/components/Pricing";
import TrustedBy from "@/components/TrustedBy";
import FAQ from "@/components/FAQ";
import MobileAppPromo from "@/components/MobileAppPromo";
import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const Index = () => {
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) {
      const id = hash.replace('#', '');
      const el = document.getElementById(id);
      if (el) {
        // defer to ensure sections are mounted
        requestAnimationFrame(() => {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
      }
    }
  }, [hash]);
  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main>
        <Hero />
        <TrustedBy />
        <Features />
        <Testimonials />
        <Pricing />
        <FAQ />
        <MobileAppPromo />
      </main>
      <Footer />
      <ChatWidget />
    </div>
  );
};

export default Index;
