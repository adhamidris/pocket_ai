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

const Index = () => {
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
