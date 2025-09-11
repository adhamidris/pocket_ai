export const track = (event: string, props?: Record<string, any>) => {
  if (__DEV__) console.log('[track]', event, props || {})
}


