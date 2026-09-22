'use client'

import { useState } from 'react'
import { ArrowRightLeft, ChevronDown, ChevronUp, FileText, Scale } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

interface ChangeDetail {
  field: string
  label: string
  old_value?: unknown
  new_value?: unknown
}

interface ArticleChange {
  article_number: string
  article_title: string
  change_type: 'modified' | 'added' | 'removed'
  old_content?: string
  new_content?: string
  diff_lines: Array<{
    type: 'unchanged' | 'added' | 'removed'
    content: string
    line_number: number
  }>
}

interface CrawlerDiffViewProps {
  candidateId: string
  detectedChanges: string[]
  changeDetails?: ChangeDetail[]
  articleChanges?: ArticleChange[]
}

export function CrawlerDiffView({
  detectedChanges,
  changeDetails,
  articleChanges,
}: CrawlerDiffViewProps) {
  const [expandedArticles, setExpandedArticles] = useState<Set<string>>(new Set())
  const [viewMode, setViewMode] = useState<'summary' | 'detailed'>('summary')

  const toggleArticle = (articleNumber: string) => {
    const newExpanded = new Set(expandedArticles)
    if (newExpanded.has(articleNumber)) {
      newExpanded.delete(articleNumber)
    } else {
      newExpanded.add(articleNumber)
    }
    setExpandedArticles(newExpanded)
  }

  const getChangeTypeColor = (changeType: string) => {
    switch (changeType) {
      case 'modified':
        return 'bg-yellow-100 text-yellow-800 border-yellow-200'
      case 'added':
        return 'bg-green-100 text-green-800 border-green-200'
      case 'removed':
        return 'bg-red-100 text-red-800 border-red-200'
      default:
        return 'bg-gray-100 text-gray-800 border-gray-200'
    }
  }

  const getChangeTypeIcon = (changeType: string) => {
    switch (changeType) {
      case 'modified':
        return <ArrowRightLeft className="h-4 w-4" />
      case 'added':
        return <FileText className="h-4 w-4" />
      case 'removed':
        return <Scale className="h-4 w-4" />
      default:
        return null
    }
  }

  if (!changeDetails?.length && !articleChanges?.length) {
    return (
      <Card>
        <CardContent className="py-6">
          <p className="text-center text-muted-foreground">
            Chưa có chi tiết thay đổi. Văn bản mới hoặc không có dữ liệu so sánh.
          </p>
          {detectedChanges.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {detectedChanges.map((change, i) => (
                <Badge key={i} variant="outline">
                  {change}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button
            variant={viewMode === 'summary' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setViewMode('summary')}
          >
            Tóm tắt
          </Button>
          <Button
            variant={viewMode === 'detailed' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setViewMode('detailed')}
          >
            Chi tiết điều khoản
          </Button>
        </div>
        <Badge variant="secondary">
          {detectedChanges.length} thay đổi phát hiện
        </Badge>
      </div>

      {viewMode === 'summary' && changeDetails && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Thay đổi thông tin mô tả</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {changeDetails.map((detail, i) => (
                <div key={i} className="grid grid-cols-3 gap-4 items-center py-2 border-b last:border-0">
                  <div className="font-medium">{detail.label}</div>
                  <div className="text-sm text-red-600 line-through">
                    {detail.old_value ? String(detail.old_value) : '(không có)'}
                  </div>
                  <div className="text-sm text-green-600">
                    {detail.new_value ? String(detail.new_value) : '(không có)'}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {viewMode === 'detailed' && articleChanges && (
        <div className="space-y-3">
          {articleChanges.map((article) => (
            <Card key={article.article_number}>
              <CardHeader className="py-3">
                <div
                  className="flex items-center justify-between cursor-pointer"
                  onClick={() => toggleArticle(article.article_number)}
                >
                  <div className="flex items-center gap-3">
                    <Badge className={getChangeTypeColor(article.change_type)}>
                      {getChangeTypeIcon(article.change_type)}
                      <span className="ml-1">
                        {article.change_type === 'modified' && 'Sửa đổi'}
                        {article.change_type === 'added' && 'Thêm mới'}
                        {article.change_type === 'removed' && 'Xóa bỏ'}
                      </span>
                    </Badge>
                    <div>
                      <CardTitle className="text-base">
                        Điều {article.article_number}
                      </CardTitle>
                      <p className="text-sm text-muted-foreground">
                        {article.article_title}
                      </p>
                    </div>
                  </div>
                  <Button variant="ghost" size="sm">
                    {expandedArticles.has(article.article_number) ? (
                      <ChevronUp className="h-4 w-4" />
                    ) : (
                      <ChevronDown className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </CardHeader>

              {expandedArticles.has(article.article_number) && (
                <CardContent>
                  <div className="rounded-md border bg-muted/50">
                    {article.diff_lines.map((line, i) => (
                      <div
                        key={i}
                        className={`flex px-3 py-1 text-sm font-mono ${
                          line.type === 'added'
                            ? 'bg-green-100 text-green-800'
                            : line.type === 'removed'
                            ? 'bg-red-100 text-red-800'
                            : ''
                        }`}
                      >
                        <span className="w-8 shrink-0 text-muted-foreground select-none">
                          {line.line_number}
                        </span>
                        <span className="shrink-0 select-none mr-2">
                          {line.type === 'added' && '+'}
                          {line.type === 'removed' && '-'}
                          {line.type === 'unchanged' && ' '}
                        </span>
                        <span>{line.content}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
