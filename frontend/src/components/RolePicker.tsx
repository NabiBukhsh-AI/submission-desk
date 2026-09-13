import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Role } from '@/lib/api'

// Which role to assess against, from the list the API offers.

export function RolePicker({
  id,
  roles,
  value,
  onChange,
  label = 'Role',
  className,
}: {
  id: string
  roles: Role[] | null
  value: string
  onChange: (roleId: string) => void
  label?: string
  className?: string
}) {
  return (
    <div className={className}>
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={onChange} disabled={!roles || roles.length === 0}>
        <SelectTrigger id={id} className="mt-1.5 w-full">
          <SelectValue placeholder={roles === null ? 'Loading roles…' : 'Choose a role'} />
        </SelectTrigger>
        <SelectContent>
          {(roles ?? []).map((role) => (
            <SelectItem key={role.role_id} value={role.role_id}>
              {role.role_title}
              <span className="ml-2 text-xs text-muted-foreground">{role.criteria} requirements</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
