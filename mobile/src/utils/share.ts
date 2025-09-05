import { Share } from 'react-native'

export async function sharePortalLink(agentId?: string) {
  const url = agentId ? `https://yourwebsite.com/app/chat/${agentId}?utm_source=app&utm_medium=share` : `https://yourwebsite.com/app?utm_source=app&utm_medium=share`
  await Share.share({ message: `Check this out: ${url}`, url })
}

