import React, { useState } from 'react'
import { View, Text, TouchableOpacity, Alert, Switch, Image } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { Modal } from '../../components/ui/Modal'
import { 
  User, 
  Mail, 
  Phone, 
  MapPin,
  Edit,
  Camera,
  Bell,
  Shield,
  Globe,
  Moon,
  Settings as SettingsIcon
} from 'lucide-react-native'

interface UserProfile {
  id: string
  name: string
  email: string
  phone?: string
  company?: string
  role: string
  location?: string
  timezone: string
  avatar?: string
  joinedDate: string
  lastActive: string
  preferences: {
    notifications: boolean
    emailUpdates: boolean
    darkMode: boolean
    language: 'en' | 'ar'
  }
}

interface ProfileSectionProps {
  onThemeToggle: () => void
  isDark: boolean
}

export const ProfileSection: React.FC<ProfileSectionProps> = ({ onThemeToggle, isDark }) => {
  const { theme } = useTheme()
  const [showEditModal, setShowEditModal] = useState(false)
  
  // Mock user profile data
  const [userProfile, setUserProfile] = useState<UserProfile>({
    id: '1',
    name: 'Alex Thompson',
    email: 'alex@company.com',
    phone: '+1 (555) 123-4567',
    company: 'TechCorp Inc.',
    role: 'Product Manager',
    location: 'San Francisco, CA',
    timezone: 'PST',
    joinedDate: '2023-06-15T00:00:00Z',
    lastActive: '2024-01-15T14:30:00Z',
    preferences: {
      notifications: true,
      emailUpdates: true,
      darkMode: isDark,
      language: 'en'
    }
  })

  const [editForm, setEditForm] = useState({
    name: userProfile.name,
    phone: userProfile.phone || '',
    company: userProfile.company || '',
    location: userProfile.location || ''
  })

  const getInitials = (name: string) => {
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
  }

  const withAlpha = (c: string, a: number) =>
    c.startsWith('hsl(')
      ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
      : c

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    })
  }

  const handleSaveProfile = () => {
    setUserProfile(prev => ({
      ...prev,
      name: editForm.name,
      phone: editForm.phone,
      company: editForm.company,
      location: editForm.location
    }))
    setShowEditModal(false)
    Alert.alert('Success', 'Profile updated successfully!')
  }

  const handlePreferenceChange = (key: keyof UserProfile['preferences'], value: boolean) => {
    setUserProfile(prev => ({
      ...prev,
      preferences: {
        ...prev.preferences,
        [key]: value
      }
    }))

    if (key === 'darkMode') {
      onThemeToggle()
    }
  }

  return (
    <View>
      {/* Profile Header */}
      <Card
        variant="flat"
        style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}
      >
        <View style={{ alignItems: 'center', marginBottom: 8 }}>
          {/* Avatar */}
          <View style={{ position: 'relative', marginBottom: 12 }}>
            {userProfile.avatar ? (
              <Image
                source={{ uri: userProfile.avatar }}
                style={{ width: 80, height: 80, borderRadius: 40, borderWidth: 0 }}
              />
            ) : (
              <View style={{
                width: 80,
                height: 80,
                backgroundColor: withAlpha(theme.color.primary, theme.dark ? 0.22 : 0.12) as any,
                borderRadius: 40,
                alignItems: 'center',
                justifyContent: 'center',
                borderWidth: 0,
                borderColor: 'transparent'
              }}>
                <Text style={{
                  color: theme.color.primary,
                  fontSize: 28,
                  fontWeight: '700'
                }}>
                  {getInitials(userProfile.name)}
                </Text>
              </View>
            )}
            
            <TouchableOpacity style={{
              position: 'absolute',
              bottom: 0,
              right: 0,
              width: 28,
              height: 28,
              backgroundColor: theme.color.primary,
              borderRadius: 14,
              alignItems: 'center',
              justifyContent: 'center',
              borderWidth: 2,
              borderColor: theme.color.card
            }}>
              <Camera size={14} color="#fff" />
            </TouchableOpacity>
          </View>

          {/* User Info */}
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 22,
            fontWeight: '700',
            marginBottom: 1
          }}>
            {userProfile.name}
          </Text>
          
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 14,
            marginBottom: 4
          }}>
            {userProfile.role} at {userProfile.company}
          </Text>

          {/* Removed plan badge per request */}
        </View>

        {/* Quick Stats */}
        <View style={{
          flexDirection: 'row',
          gap: 8,
          paddingTop: 8,
          borderTopWidth: 1,
          borderTopColor: theme.color.border
        }}>
          <View style={{ alignItems: 'center', flex: 1 }}>
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 18,
              fontWeight: '700',
              marginBottom: 0
            }}>
              156
            </Text>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              Conversations
            </Text>
          </View>
          
          <View style={{ alignItems: 'center', flex: 1 }}>
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 18,
              fontWeight: '700',
              marginBottom: 0
            }}>
              98%
            </Text>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              Satisfaction
            </Text>
          </View>
          
          <View style={{ alignItems: 'center', flex: 1 }}>
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 18,
              fontWeight: '700',
              marginBottom: 0
            }}>
              45d
            </Text>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              Member Since
            </Text>
          </View>
        </View>
      </Card>

      {/* Contact Information */}
      <Card
        variant="flat"
        style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}
      >
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 8
        }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 16,
            fontWeight: '600'
          }}>
            Contact Information
          </Text>
          <TouchableOpacity
            onPress={() => setShowEditModal(true)}
            style={{ padding: 4 }}
            hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
          >
            <Edit size={16} color={theme.color.mutedForeground as any} />
          </TouchableOpacity>
        </View>
        
        <View style={{ gap: 6 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 }}>
            <Mail size={16} color={theme.color.mutedForeground} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 14,
              flex: 1
            }}>
              {userProfile.email}
            </Text>
          </View>
          
          {userProfile.phone && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 }}>
              <Phone size={16} color={theme.color.mutedForeground} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                flex: 1
              }}>
                {userProfile.phone}
              </Text>
            </View>
          )}
          
          {userProfile.location && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 }}>
              <MapPin size={16} color={theme.color.mutedForeground} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                flex: 1
              }}>
                {userProfile.location}
              </Text>
            </View>
          )}
        </View>
      </Card>

      {/* Preferences */}
      <Card
        variant="flat"
        style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}
      >
        <Text style={{
          color: theme.color.cardForeground,
          fontSize: 16,
          fontWeight: '600',
          marginBottom: 8
        }}>
          Preferences
        </Text>
        
        <View style={{ gap: 6 }}>
          <View style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            paddingVertical: 8
          }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1 }}>
              <Bell size={16} color={theme.color.mutedForeground} />
              <View style={{ flex: 1 }}>
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  fontWeight: '500'
                }}>
                  Push Notifications
                </Text>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 12
                }}>
                  Get notified about new conversations
                </Text>
              </View>
            </View>
            <Switch
              value={userProfile.preferences.notifications}
              onValueChange={(value) => handlePreferenceChange('notifications', value)}
              trackColor={{
                false: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any,
                true: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any
              }}
              thumbColor={userProfile.preferences.notifications ? theme.color.primary : theme.color.mutedForeground}
            />
          </View>

          <View style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            paddingVertical: 8
          }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1 }}>
              <Mail size={16} color={theme.color.mutedForeground} />
              <View style={{ flex: 1 }}>
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  fontWeight: '500'
                }}>
                  Email Updates
                </Text>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 12
                }}>
                  Receive product updates and tips
                </Text>
              </View>
            </View>
            <Switch
              value={userProfile.preferences.emailUpdates}
              onValueChange={(value) => handlePreferenceChange('emailUpdates', value)}
              trackColor={{
                false: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any,
                true: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any
              }}
              thumbColor={userProfile.preferences.emailUpdates ? theme.color.primary : theme.color.mutedForeground}
            />
          </View>

          <View style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            paddingVertical: 8
          }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1 }}>
              <Moon size={16} color={theme.color.mutedForeground} />
              <View style={{ flex: 1 }}>
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  fontWeight: '500'
                }}>
                  Dark Mode
                </Text>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 12
                }}>
                  Use dark theme interface
                </Text>
              </View>
            </View>
            <Switch
              value={isDark}
              onValueChange={onThemeToggle}
              trackColor={{
                false: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any,
                true: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any
              }}
              thumbColor={isDark ? theme.color.primary : theme.color.mutedForeground}
            />
          </View>
        </View>
      </Card>

      {/* Account Actions */}
      <Card
        variant="flat"
        style={{ backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}
      >
        <Text style={{
          color: theme.color.cardForeground,
          fontSize: 16,
          fontWeight: '600',
          marginBottom: 8
        }}>
          Account
        </Text>
        
        <View style={{ gap: 6 }}>
          <TouchableOpacity style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            paddingVertical: 8
          }}>
            <Shield size={16} color={theme.color.mutedForeground} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 14,
              flex: 1
            }}>
              Security Settings
            </Text>
          </TouchableOpacity>
          
          <TouchableOpacity style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            paddingVertical: 8
          }}>
            <Globe size={16} color={theme.color.mutedForeground} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 14,
              flex: 1
            }}>
              Language & Region
            </Text>
          </TouchableOpacity>
          
          <TouchableOpacity style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            paddingVertical: 8
          }}>
            <SettingsIcon size={16} color={theme.color.mutedForeground} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 14,
              flex: 1
            }}>
              Advanced Settings
            </Text>
          </TouchableOpacity>
        </View>
      </Card>

      {/* Edit Profile Modal */}
      <Modal visible={showEditModal} onClose={() => setShowEditModal(false)} title="Edit Profile">
        <View style={{ gap: 16 }}>
          <Input
            label="Full Name"
            value={editForm.name}
            onChangeText={(text) => setEditForm(prev => ({ ...prev, name: text }))}
            icon={<User size={20} color={theme.color.mutedForeground} />}
          />
          
          <Input
            label="Phone Number"
            value={editForm.phone}
            onChangeText={(text) => setEditForm(prev => ({ ...prev, phone: text }))}
            icon={<Phone size={20} color={theme.color.mutedForeground} />}
            keyboardType="phone-pad"
          />
          
          <Input
            label="Company"
            value={editForm.company}
            onChangeText={(text) => setEditForm(prev => ({ ...prev, company: text }))}
          />
          
          <Input
            label="Location"
            value={editForm.location}
            onChangeText={(text) => setEditForm(prev => ({ ...prev, location: text }))}
            icon={<MapPin size={20} color={theme.color.mutedForeground} />}
          />
          
          <View style={{ flexDirection: 'row', gap: 12, marginTop: 20 }}>
            <Button
              title="Save Changes"
              onPress={handleSaveProfile}
              variant="premium"
              size="lg"
            />
            <Button
              title="Cancel"
              onPress={() => setShowEditModal(false)}
              variant="ghost"
              size="lg"
            />
          </View>
        </View>
      </Modal>
    </View>
  )
}
