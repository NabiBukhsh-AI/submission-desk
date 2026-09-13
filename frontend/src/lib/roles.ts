import { useEffect, useState } from 'react'
import { api, type Role } from '@/lib/api'

// The roles the API offers. A role that is not in the list cannot be chosen,
// so a run can never name a rubric that does not exist.
export function useRoles(): Role[] | null {
  const [roles, setRoles] = useState<Role[] | null>(null)
  useEffect(() => {
    api.roles().then(setRoles).catch(() => setRoles([]))
  }, [])
  return roles
}
