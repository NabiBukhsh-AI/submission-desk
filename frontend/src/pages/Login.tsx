import { useState, type FormEvent } from 'react'
import { LogIn, UserPlus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError, api } from '@/lib/api'

// Matches MIN_PASSWORD_CHARS on the server; stated before typing, not after.
const MIN_PASSWORD_CHARS = 10

// One form, two modes. On a fresh deployment the first visitor creates the
// admin account; afterwards the same fields sign in.
export function Login({ setupRequired, onSignedIn }: { setupRequired: boolean; onSignedIn: (user: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { user } = setupRequired
        ? await api.auth.setup(username, password)
        : await api.auth.login(username, password)
      onSignedIn(user)
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : 'The API is not reachable.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-sm pt-12">
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">{setupRequired ? 'Create the admin account' : 'Sign in'}</CardTitle>
          <CardDescription>
            {setupRequired
              ? 'This is a fresh deployment. The first account created here is the one admin; there is no other.'
              : 'Reviewing and settings are behind the admin account.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                autoComplete="username"
                autoFocus
                required
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete={setupRequired ? 'new-password' : 'current-password'}
                required
                minLength={setupRequired ? MIN_PASSWORD_CHARS : undefined}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              {setupRequired && (
                <p className="text-xs text-muted-foreground">At least {MIN_PASSWORD_CHARS} characters.</p>
              )}
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={busy}>
              {setupRequired ? <UserPlus aria-hidden="true" /> : <LogIn aria-hidden="true" />}
              {setupRequired ? 'Create account and sign in' : 'Sign in'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
