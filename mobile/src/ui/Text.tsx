import React from 'react'
import { Text as RNText, TextProps as RNTextProps, TextStyle } from 'react-native'

export interface TextProps extends RNTextProps {
  size?: number
  weight?: TextStyle['fontWeight']
  color?: string
  align?: TextStyle['textAlign']
  lineHeight?: number
}

export const Text: React.FC<TextProps> = ({
  size,
  weight,
  color,
  align,
  lineHeight,
  style,
  children,
  ...rest
}) => {
  const computed: TextStyle = {
    fontSize: size,
    fontWeight: weight,
    color,
    textAlign: align,
    lineHeight,
  }

  return (
    <RNText style={[computed, style]} {...rest}>
      {children}
    </RNText>
  )
}

export default Text


