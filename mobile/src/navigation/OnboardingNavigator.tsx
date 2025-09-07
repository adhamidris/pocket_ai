import React, { useRef, useState } from 'react'
import { View, Animated, Easing, useWindowDimensions } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { WelcomeScreen } from '../screens/onboarding/Welcome'
import { HowItWorksScreen } from '../screens/onboarding/HowItWorks'
import { ChatPreviewScreen } from '../screens/onboarding/ChatPreview'
import { RegisterScreen } from '../screens/onboarding/Register'
import { SkipLoginScreen } from '../screens/onboarding/SkipLogin'
import { HowItWorksAgentScreen } from '../screens/onboarding/HowItWorksAgent'
import { ShareAnywhereScreen } from '../screens/onboarding/ShareAnywhere'
import { useTheme } from '../providers/ThemeProvider'

export const OnboardingNavigator: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
  const { theme } = useTheme()
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
      {/* Base: current screen */}
      <View style={{ flex: 1 }}>
        {showSkipLogin ? (
          <SkipLoginScreen onBack={() => setShowSkipLogin(false)} onRegister={skipToRegister} onLogin={() => {}} />
        ) : (
        <>
        {currentStep === 0 && <WelcomeScreen onNext={handleNext} onSkip={handleSkip} />}
        {currentStep === 1 && <HowItWorksScreen onNext={handleNext} onBack={handleBack} />}
        {currentStep === 2 && <HowItWorksAgentScreen onNext={handleNext} onBack={handleBack} />}
        {currentStep === 3 && <ShareAnywhereScreen onNext={handleNext} onBack={handleBack} />}
        {currentStep === 4 && !showRegister && (
          <ChatPreviewScreen onComplete={handleSkip} onBack={handleBack} onRegister={() => setShowRegister(true)} />
        )}
        {currentStep === 4 && showRegister && (
          <RegisterScreen onBack={() => setShowRegister(false)} onLogin={() => { setShowRegister(false); setShowSkipLogin(true) }} />
        )}
        </>
        )}
      </View>
      {/* Overlay: incoming screen slides over */}
      {transitioningStep !== null && (
        <Animated.View style={{ position: 'absolute', inset: 0, transform: [{ translateX: incomingX }] }}>
          {transitioningStep === 0 && <WelcomeScreen onNext={handleNext} onSkip={handleSkip} />}
          {transitioningStep === 1 && <HowItWorksScreen onNext={handleNext} onBack={handleBack} />}
          {transitioningStep === 2 && <HowItWorksAgentScreen onNext={handleNext} onBack={handleBack} />}
          {transitioningStep === 3 && <ShareAnywhereScreen onNext={handleNext} onBack={handleBack} />}
          {transitioningStep === 4 && !showRegister && (
            <ChatPreviewScreen onComplete={handleSkip} onBack={handleBack} onRegister={() => setShowRegister(true)} />
          )}
          {transitioningStep === 4 && showRegister && (
            <RegisterScreen onBack={() => setShowRegister(false)} onLogin={() => { setShowRegister(false); setShowSkipLogin(true) }} />
          )}
        </Animated.View>
      )}
      {/* Sliding out current (optional visual); we keep base static to avoid blank frames */}
      {isTransitioning && (
        <Animated.View pointerEvents="none" style={{ position: 'absolute', inset: 0, transform: [{ translateX: currentX }], opacity: 0.96 }}>
          {currentStep === 0 && <WelcomeScreen onNext={handleNext} onSkip={handleSkip} />}
          {currentStep === 1 && <HowItWorksScreen onNext={handleNext} onBack={handleBack} />}
          {currentStep === 2 && <HowItWorksAgentScreen onNext={handleNext} onBack={handleBack} />}
          {currentStep === 3 && <ShareAnywhereScreen onNext={handleNext} onBack={handleBack} />}
          {currentStep === 4 && !showRegister && (
            <ChatPreviewScreen onComplete={handleSkip} onBack={handleBack} onRegister={() => setShowRegister(true)} />
          )}
          {currentStep === 4 && showRegister && (
            <RegisterScreen onBack={() => setShowRegister(false)} />
          )}
        </Animated.View>
      )}
    </View>
  )
}
