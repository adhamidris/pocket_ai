import React from 'react'
import { View, ViewProps, ViewStyle } from 'react-native'

type Spacing = number | undefined

export interface BoxProps extends ViewProps {
  // Layout
  row?: boolean
  column?: boolean
  align?: ViewStyle['alignItems']
  justify?: ViewStyle['justifyContent']
  wrap?: ViewStyle['flexWrap']
  gap?: number

  // Flex
  flex?: number
  flexGrow?: number
  flexShrink?: number
  flexBasis?: ViewStyle['flexBasis']

  // Spacing shorthands (margin/padding)
  m?: Spacing; mt?: Spacing; mr?: Spacing; mb?: Spacing; ml?: Spacing; mx?: Spacing; my?: Spacing
  p?: Spacing; pt?: Spacing; pr?: Spacing; pb?: Spacing; pl?: Spacing; px?: Spacing; py?: Spacing

  // Size
  w?: number | string
  h?: number | string

  // Border & radius
  radius?: number
  borderWidth?: number
  borderColor?: string
  backgroundColor?: string

  // Positioning
  position?: ViewStyle['position']
  top?: number
  right?: number
  bottom?: number
  left?: number
}

const computeSpacing = (value?: Spacing) => (typeof value === 'number' ? value : undefined)

export const Box: React.FC<BoxProps> = ({
  row,
  column,
  align,
  justify,
  wrap,
  gap,
  flex,
  flexGrow,
  flexShrink,
  flexBasis,
  m, mt, mr, mb, ml, mx, my,
  p, pt, pr, pb, pl, px, py,
  w, h,
  radius,
  borderWidth,
  borderColor,
  backgroundColor,
  position,
  top,
  right,
  bottom,
  left,
  style,
  children,
  ...rest
}) => {
  const computedStyle: ViewStyle = {
    // layout
    flexDirection: row ? 'row' : column ? 'column' : undefined,
    alignItems: align,
    justifyContent: justify,
    flexWrap: wrap,
    gap,

    // flex
    flex,
    flexGrow,
    flexShrink,
    flexBasis,

    // spacing
    margin: computeSpacing(m),
    marginTop: computeSpacing(my ?? mt),
    marginBottom: computeSpacing(my ?? mb),
    marginLeft: computeSpacing(mx ?? ml),
    marginRight: computeSpacing(mx ?? mr),
    padding: computeSpacing(p),
    paddingTop: computeSpacing(py ?? pt),
    paddingBottom: computeSpacing(py ?? pb),
    paddingLeft: computeSpacing(px ?? pl),
    paddingRight: computeSpacing(px ?? pr),

    // size
    width: w as any,
    height: h as any,

    // visuals
    borderRadius: radius,
    borderWidth,
    borderColor,
    backgroundColor,

    // positioning
    position,
    top,
    right,
    bottom,
    left,
  }

  return (
    <View style={[computedStyle, style]} {...rest}>
      {children}
    </View>
  )
}

export default Box


