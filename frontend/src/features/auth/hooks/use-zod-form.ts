'use client'

import { useState } from 'react'
import type * as z from 'zod/mini'

// 로그인/회원가입 폼 공통: 값·필드 오류·폼 오류·제출 중 상태 + zod 검사.
// 제출 시 전체 검사 → 필드별 첫 메시지. 한 번 오류 난 필드는 입력 중 재검사(AGENTS.md 검증 타이밍).
export function useZodForm<S extends z.ZodMiniType<Record<string, unknown>>>(schema: S, initial: z.input<S>) {
  type Values = z.input<S>
  type Errors = Partial<Record<keyof Values & string, string>>
  const [values, setValues] = useState<Values>(initial)
  const [errors, setErrors] = useState<Errors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const validate = (v: Values = values) => {
    const r = schema.safeParse(v)
    const e: Errors = {}
    if (!r.success) for (const issue of r.error.issues) e[issue.path[0] as keyof Values & string] ??= issue.message
    setErrors(e)
    return r.success ? (r.data as z.output<S>) : null
  }
  const set = (k: keyof Values & string, value: unknown) => {
    const v = Object.assign({}, values, { [k]: value }) as Values // z.input<S> 가 객체임을 TS 가 모르므로 스프레드 대신
    setValues(v)
    if (errors[k]) validate(v)
  }
  const describedBy = (k: keyof Values & string) => (errors[k] ? `${k}-error` : undefined)

  return { values, errors, formError, setFormError, pending, setPending, validate, set, describedBy }
}
