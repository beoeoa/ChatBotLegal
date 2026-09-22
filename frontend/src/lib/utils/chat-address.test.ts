import { describe, expect, it } from 'vitest'
import { answerAlreadyHasSalutation, answerSalutation, chatStarterQuestions, welcomeMessage, registeredAnswer } from './chat-address'

describe('role-aware chat address', () => {
  it('corrects a model greeting from registered gender without editing body quotations', () => {
    expect(registeredAnswer('Thưa anh Nguyễn Văn An, Hồ sơ gồm...', { gender: 'female' })).toBe('Thưa chị, Hồ sơ gồm...')
    expect(registeredAnswer('Trong thư ghi “Thưa anh, ...”.', { gender: 'female' })).toBe('Trong thư ghi “Thưa anh, ...”.')
  })
  it('addresses a citizen politely by profile name', () => {
    expect(answerSalutation('citizen', { fullName: 'Nguyễn Văn An' }))
      .toBe('Thưa anh/chị,')
    expect(welcomeMessage('citizen', { fullName: 'Nguyễn Văn An' }).title)
      .toBe('Xin chào anh/chị Nguyễn Văn An.')
  })

  it('does not expose test identities or usernames as a citizen name', () => {
    expect(answerSalutation('citizen', {
      fullName: 'Nguoi Dan Test',
      username: 'citizen01',
    })).toBe('Thưa anh/chị,')
    expect(welcomeMessage('citizen', { username: 'citizen01' }).title)
      .toBe('Xin chào anh/chị.')
  })

  it('addresses an officer by title, name and department', () => {
    const identity = {
      jobTitle: 'Chuyên viên',
      fullName: 'Trần Minh Hà',
      department: 'Bộ phận Tư pháp - Hộ tịch',
    }
    expect(answerSalutation('officer', identity))
      .toBe('Thưa anh/chị,')
    expect(welcomeMessage('officer', identity).title)
      .toBe('Kính chào Chuyên viên Trần Minh Hà thuộc Bộ phận Tư pháp - Hộ tịch.')
  })

  it('uses the assigned account domain when an officer profile has no department', () => {
    expect(answerSalutation('officer', { username: 'officer_hotich' }))
      .toBe('Thưa anh/chị,')
    expect(welcomeMessage('officer', { username: 'officer_khieunai' }).title)
      .toBe('Kính chào cán bộ thuộc Khiếu nại - Tố cáo.')
  })

  it('avoids repeating a generic officer name and department already contained in the title', () => {
    const identity = {
      jobTitle: 'Cán bộ phụ trách Hộ tịch - Chứng thực',
      fullName: 'Cán bộ Hộ tịch - Chứng thực',
      department: 'Hộ tịch - chứng thực',
      username: 'officer_hotich',
    }
    expect(answerSalutation('officer', identity))
      .toBe('Thưa anh/chị,')
  })

  it('recognizes an existing model-authored salutation', () => {
    expect(answerAlreadyHasSalutation('## Kính thưa Chuyên viên Trần Minh Hà,\nNội dung')).toBe(true)
    expect(answerAlreadyHasSalutation('Anh Bảy, tôi xin lỗi vì đã gọi sai.')).toBe(true)
    expect(answerAlreadyHasSalutation('Chào anh Bảy, rất vui được gặp anh.')).toBe(true)
    expect(answerAlreadyHasSalutation('Dạ, anh Bảy đã dặn tôi trả lời ngắn.')).toBe(true)
    expect(answerAlreadyHasSalutation('Anh/chị cần nộp hồ sơ tại UBND phường.')).toBe(false)
    expect(answerAlreadyHasSalutation('Nội dung trả lời')).toBe(false)
  })

  it('offers role and officer-domain scoped starter questions', () => {
    expect(chatStarterQuestions('citizen')).toHaveLength(4)
    const officer = chatStarterQuestions('officer', { username: 'officer_cutru' })
    expect(officer).toHaveLength(4)
    expect(officer.every((question) => /tạm trú|cư trú/i.test(question))).toBe(true)
    expect(chatStarterQuestions('admin')).toEqual([])
  })
})

it('uses only registered gender for answer address', () => {
  expect(answerSalutation('citizen', { gender: 'female', fullName: 'Nguyễn Văn An' })).toBe('Thưa chị,')
  expect(answerSalutation('officer', { gender: 'male' })).toBe('Thưa anh,')
  expect(answerSalutation('admin', { fullName: 'Nguyễn Thị Lan' })).toBe('Thưa anh/chị,')
})
