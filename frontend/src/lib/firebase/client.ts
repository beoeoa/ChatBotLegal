import { getApp, getApps, initializeApp, type FirebaseApp } from '@firebase/app'
import { getAuth, type Auth } from '@firebase/auth'

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
}

const firebaseConfigComplete = Object.values(firebaseConfig).every(
  (value) => typeof value === 'string' && value.trim().length > 0,
)

export const firebaseEnabled =
  process.env.NEXT_PUBLIC_FIREBASE_ENABLED === 'true' && firebaseConfigComplete

let firebaseApp: FirebaseApp | null = null
let firebaseAuth: Auth | null = null

export function getFirebaseAuth(): Auth {
  if (!firebaseEnabled) {
    throw new Error('Firebase client authentication is disabled')
  }
  if (!firebaseApp) {
    firebaseApp = getApps().length ? getApp() : initializeApp(firebaseConfig)
  }
  if (!firebaseAuth) {
    firebaseAuth = getAuth(firebaseApp)
    firebaseAuth.useDeviceLanguage()
  }
  return firebaseAuth
}
