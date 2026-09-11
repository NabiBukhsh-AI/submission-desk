import { createContext, useContext } from 'react'
import type { Vocabulary } from '@/lib/api'

// The interface's words come from the API so this client never invents its
// own. Loaded once in App; every page reads them from here.
export const VocabularyContext = createContext<Vocabulary | null>(null)
export const useVocabulary = () => useContext(VocabularyContext)
