import React, { useRef, useState } from 'react'
import { View, Animated, Easing, useWindowDimensions, TouchableOpacity } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { WelcomeScreen } from '../screens/onboarding/Welcome'
import { HowItWorksScreen } from '../screens/onboarding/HowItWorks'
import { ChatPreviewScreen } from '../screens/onboarding/ChatPreview'
import { RegisterScreen } from '../screens/onboarding/Register'
import { SkipLoginScreen } from '../screens/onboarding/SkipLogin'
import { HowItWorksAgentScreen } from '../screens/onboarding/HowItWorksAgent'
import { ShareAnywhereScreen } from '../screens/onboarding/ShareAnywhere'
import { useTheme } from '../providers/ThemeProvider'
import { Sun, Moon } from 'lucide-react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

export const OnboardingNavigator: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
  const { theme, toggle } = useTheme()
  const insets = useSafeAreaInsets()
  const { width } = useWindowDimensions()
  const [currentStep, setCurrentStep] = useState(0)
  const [showRegister, setShowRegister] = useState(false)
  const [transitioningStep, setTransitioningStep] = useState<number | null>(null)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const currentX = useRef(new Animated.Value(0)).current
  const incomingX = useRef(new Animated.Value(0)).current

  const [showSkipLogin, setShowSkipLogin] = useState(false)
  const handleSkip = async () => {
    setShowSkipLogin(true)
  }

  const skipToRegister = () => {
    setShowSkipLogin(false)
    setShowRegister(true)
    setCurrentStep(4)
  }

  const handleNext = () => {
    if (isTransitioning) return
    if (currentStep < 4) {
      const next = currentStep + 1
      setIsTransitioning(true)
      setTransitioningStep(next)
      currentX.setValue(0)
      incomingX.setValue(width)
      Animated.parallel([
        Animated.timing(currentX, { toValue: -width, duration: 280, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
        Animated.timing(incomingX, { toValue: 0, duration: 320, easing: Easing.out(Easing.cubic), useNativeDriver: true })
      ]).start(() => {
        setCurrentStep(next)
        setTransitioningStep(null)
        setIsTransitioning(false)
      })
    } else {
      handleSkip() // Complete onboarding
    }
  }

  const handleBack = () => {
    if (isTransitioning) return
    if (currentStep > 0) {
      const prev = currentStep - 1
      setIsTransitioning(true)
      setTransitioningStep(prev)
      currentX.setValue(0)
      incomingX.setValue(-width)
      Animated.parallel([
        Animated.timing(currentX, { toValue: width, duration: 280, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
        Animated.timing(incomingX, { toValue: 0, duration: 300, easing: Easing.out(Easing.cubic), useNativeDriver: true })
      ]).start(() => {
        setCurrentStep(prev)
        setTransitioningStep(null)
        setIsTransitioning(false)
      })
    }
  }

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.background, overflow: 'hidden' }}>
      {/* Theme toggle */}
      <View style={{ position: 'absolute', top: insets.top + 8, right: 12, zIndex: 20 }}>
        <TouchableOpacity
          onPress={toggle}
          activeOpacity={0.85}
          style={{
            padding: 8,
            borderRadius: 16,
            backgroundColor: 'transparent',
            borderWidth: 0,
            borderColor: 'transparent',
            shadowColor: 'transparent',
            shadowOpacity: 0,
            shadowRadius: 0,
            elevation: 0,
          }}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          {theme.dark ? <Sun size={18} color={theme.color.cardForeground as any} /> : <Moon size={18} color={theme.color.cardForeground as any} />}
        </TouchableOpacity>
      </View>
      {/* Base: current screen */}
      <View style={{ flex: 1 }}>
        {showSkipLogin ? (
          <SkipLoginScreen onBack={() => setShowSkipLogin(false)} onRegister={skipToRegister} onLogin={() => {}} />
        ) : (
        <>
        {currentStep === 0 && <WelcomeScreen onNext={handleNext} onSkip={handleSkip} />}
        {currentStep === 1 && <HowItWorksScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
        {currentStep === 2 && <HowItWorksAgentScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
        {currentStep === 3 && <ShareAnywhereScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
        {currentStep === 4 && !showRegister && (
          <ChatPreviewScreen onComplete={handleSkip} onBack={handleBack} onRegister={() => setShowRegister(true)} />
        )}
        {currentStep === 4 && showRegister && (
          <RegisterScreen onBack={() => setShowRegister(false)} onLogin={() => { setShowRegister(false); setShowSkipLogin(true) }} onComplete={onComplete} />
        )}
        </>
        )}
      </View>
      {/* Overlay: incoming screen slides over */}
      {transitioningStep !== null && (
        <Animated.View style={{ position: 'absolute', inset: 0, transform: [{ translateX: incomingX }] }}>
          {transitioningStep === 0 && <WelcomeScreen onNext={handleNext} onSkip={handleSkip} />}
          {transitioningStep === 1 && <HowItWorksScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
          {transitioningStep === 2 && <HowItWorksAgentScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
          {transitioningStep === 3 && <ShareAnywhereScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
          {transitioningStep === 4 && !showRegister && (
            <ChatPreviewScreen onComplete={handleSkip} onBack={handleBack} onRegister={() => setShowRegister(true)} />
          )}
          {transitioningStep === 4 && showRegister && (
            <RegisterScreen onBack={() => setShowRegister(false)} onLogin={() => { setShowRegister(false); setShowSkipLogin(true) }} onComplete={onComplete} />
          )}
        </Animated.View>
      )}
      {/* Sliding out current (optional visual); we keep base static to avoid blank frames */}
      {isTransitioning && (
        <Animated.View pointerEvents="none" style={{ position: 'absolute', inset: 0, transform: [{ translateX: currentX }], opacity: 0.96 }}>
          {currentStep === 0 && <WelcomeScreen onNext={handleNext} onSkip={handleSkip} />}
          {currentStep === 1 && <HowItWorksScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
          {currentStep === 2 && <HowItWorksAgentScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
          {currentStep === 3 && <ShareAnywhereScreen onNext={handleNext} onBack={handleBack} onSkipToRegister={skipToRegister} />}
          {currentStep === 4 && !showRegister && (
            <ChatPreviewScreen onComplete={handleSkip} onBack={handleBack} onRegister={() => setShowRegister(true)} />
          )}
          {currentStep === 4 && showRegister && (
            <RegisterScreen onBack={() => setShowRegister(false)} onComplete={onComplete} />
          )}
        </Animated.View>
      )}
    </View>
  )
}
