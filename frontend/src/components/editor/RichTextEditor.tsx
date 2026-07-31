/**
 * Shared TipTap rich-text editor.
 *
 * Controlled on **markdown**: `value` is markdown in, `onChange` is markdown
 * out — TipTap only ever sees HTML (see `markdown-bridge.ts`). The document
 * schema is deliberately constrained to what both markdown renderers (the
 * React `Markdown` component and the widget's `renderMarkdown`) understand:
 * h1–h4, bold, italic, inline code, code fences, lists, blockquote, link,
 * image and horizontal rules.
 */

import { Image } from '@tiptap/extension-image'
import { Placeholder } from '@tiptap/extensions'
import { EditorContent, useEditor, useEditorState } from '@tiptap/react'
import { StarterKit } from '@tiptap/starter-kit'
import {
  Bold,
  Code,
  Heading1,
  Heading2,
  Heading3,
  Heading4,
  ImagePlus,
  Italic,
  Link2,
  Link2Off,
  List,
  ListOrdered,
  Loader2,
  Quote,
  Redo2,
  SquareCode,
  Undo2,
  Upload,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { ApiError, api, ws } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { Toggle } from '@/components/ui/toggle'
import { cn } from '@/lib/utils'

import { htmlToMarkdown, markdownToHtml } from './markdown-bridge'

export type RichTextEditorVariant = 'full' | 'compact'

export interface RichTextEditorProps {
  /** Markdown — the persisted format. */
  value: string
  /** Called with markdown whenever the document changes. */
  onChange: (markdown: string) => void
  /** `full` shows the whole toolbar, `compact` only inline marks + link. */
  variant?: RichTextEditorVariant
  placeholder?: string
  className?: string
  /** Read-only rendering (still selectable, no toolbar actions). */
  disabled?: boolean
  /** Accessible name for the editable region. */
  ariaLabel?: string
}

/** Uploaded image responses from `POST /w/{ws}/files`. */
interface UploadedFile {
  url: string
}

const PROSE_CLASS = [
  'min-h-40 w-full rounded-b-md px-3 py-2 text-sm outline-none',
  '[&_h1]:mt-2 [&_h1]:text-xl [&_h1]:font-semibold',
  '[&_h2]:mt-2 [&_h2]:text-lg [&_h2]:font-semibold',
  '[&_h3]:mt-2 [&_h3]:text-base [&_h3]:font-semibold',
  '[&_h4]:mt-2 [&_h4]:text-sm [&_h4]:font-semibold',
  '[&_p]:leading-relaxed',
  '[&_ul]:ml-5 [&_ul]:list-disc [&_ol]:ml-5 [&_ol]:list-decimal',
  '[&_li>p]:inline',
  '[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
  '[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_pre]:font-mono [&_pre]:text-xs',
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em]',
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0',
  '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2',
  '[&_img]:max-w-full [&_img]:rounded-md',
  '[&_hr]:my-3 [&_hr]:border-border',
  // Placeholder: TipTap decorates the empty document with `.is-editor-empty`
  // plus a `data-placeholder` attribute, which we surface through a ::before pseudo.
  '[&_.is-editor-empty::before]:pointer-events-none',
  '[&_.is-editor-empty::before]:float-left',
  '[&_.is-editor-empty::before]:h-0',
  '[&_.is-editor-empty::before]:text-muted-foreground',
  '[&_.is-editor-empty::before]:content-[attr(data-placeholder)]',
].join(' ')

export function RichTextEditor({
  value,
  onChange,
  variant = 'full',
  placeholder = 'Write something…',
  className,
  disabled = false,
  ariaLabel = 'Rich text editor',
}: RichTextEditorProps) {
  // Markdown we emitted, oldest first. A controlled parent echoes each value
  // back through `value`, and React delivers those renders in order — so a
  // render can carry a *stale* emission while the editor has already moved on.
  // Matching against the whole queue (instead of just the newest value) is what
  // keeps fast typing from being clobbered by its own echo.
  const emitted = useRef<string[]>([value])
  // `useEditor` diffs its options by identity and re-applies them mid-typing
  // when they change, so everything non-primitive has to stay referentially
  // stable. `content` is the *initial* document only; later `value` changes go
  // through the sync effect below.
  const initialContent = useRef(markdownToHtml(value))

  const extensions = useMemo(
    () => [
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4] },
        link: { openOnClick: false, autolink: false, HTMLAttributes: { rel: 'noreferrer' } },
        // Marks with no markdown representation in the constrained set.
        strike: false,
        underline: false,
      }),
      Image.configure({ allowBase64: false }),
      // `showOnlyCurrent: false` keeps the hint visible while the editor is
      // blurred; `is-editor-empty` only lands on a wholly empty document, so it
      // never leaks into empty list items or headings.
      Placeholder.configure({ placeholder, showOnlyCurrent: false }),
    ],
    [placeholder]
  )

  const editorProps = useMemo(
    () => ({
      attributes: {
        class: PROSE_CLASS,
        role: 'textbox',
        'aria-multiline': 'true',
        'aria-label': ariaLabel,
      },
    }),
    [ariaLabel]
  )

  const editor = useEditor({
    extensions,
    editorProps,
    content: initialContent.current,
    editable: !disabled,
    onUpdate: ({ editor: instance }) => {
      const markdown = htmlToMarkdown(instance.getHTML())
      emitted.current.push(markdown)
      if (emitted.current.length > 100) emitted.current.shift()
      onChange(markdown)
    },
  })

  // External writes (markdown tab, loading a document) replace the document.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    const echo = emitted.current.lastIndexOf(value)
    if (echo !== -1) {
      emitted.current.splice(0, echo + 1)
      return
    }
    editor.commands.setContent(markdownToHtml(value), { emitUpdate: false })
  }, [editor, value])

  useEffect(() => {
    if (editor && !editor.isDestroyed) editor.setEditable(!disabled, false)
  }, [editor, disabled])

  const active = useEditorState({
    editor,
    selector: ({ editor: instance }) => ({
      bold: instance.isActive('bold'),
      italic: instance.isActive('italic'),
      code: instance.isActive('code'),
      codeBlock: instance.isActive('codeBlock'),
      bulletList: instance.isActive('bulletList'),
      orderedList: instance.isActive('orderedList'),
      blockquote: instance.isActive('blockquote'),
      link: instance.isActive('link'),
      h1: instance.isActive('heading', { level: 1 }),
      h2: instance.isActive('heading', { level: 2 }),
      h3: instance.isActive('heading', { level: 3 }),
      h4: instance.isActive('heading', { level: 4 }),
      canUndo: instance.can().undo(),
      canRedo: instance.can().redo(),
    }),
  })

  const isFull = variant === 'full'

  return (
    <div
      className={cn(
        'rounded-md border bg-background focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50',
        disabled && 'opacity-70',
        className
      )}
      data-variant={variant}
    >
      <div
        role="toolbar"
        aria-label="Formatting"
        aria-orientation="horizontal"
        className="flex flex-wrap items-center gap-0.5 rounded-t-md border-b bg-muted/40 px-1 py-1"
      >
        {isFull ? (
          <>
            <ToolbarToggle
              label="Heading 1"
              icon={Heading1}
              pressed={active?.h1 ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
            />
            <ToolbarToggle
              label="Heading 2"
              icon={Heading2}
              pressed={active?.h2 ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
            />
            <ToolbarToggle
              label="Heading 3"
              icon={Heading3}
              pressed={active?.h3 ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
            />
            <ToolbarToggle
              label="Heading 4"
              icon={Heading4}
              pressed={active?.h4 ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleHeading({ level: 4 }).run()}
            />
            <Separator orientation="vertical" className="mx-1 !h-5" />
          </>
        ) : null}

        <ToolbarToggle
          label="Bold"
          icon={Bold}
          pressed={active?.bold ?? false}
          disabled={disabled}
          onToggle={() => editor.chain().focus().toggleBold().run()}
        />
        <ToolbarToggle
          label="Italic"
          icon={Italic}
          pressed={active?.italic ?? false}
          disabled={disabled}
          onToggle={() => editor.chain().focus().toggleItalic().run()}
        />
        <ToolbarToggle
          label="Inline code"
          icon={Code}
          pressed={active?.code ?? false}
          disabled={disabled}
          onToggle={() => editor.chain().focus().toggleCode().run()}
        />

        {isFull ? (
          <>
            <ToolbarToggle
              label="Code block"
              icon={SquareCode}
              pressed={active?.codeBlock ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleCodeBlock().run()}
            />
            <Separator orientation="vertical" className="mx-1 !h-5" />
            <ToolbarToggle
              label="Bullet list"
              icon={List}
              pressed={active?.bulletList ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleBulletList().run()}
            />
            <ToolbarToggle
              label="Ordered list"
              icon={ListOrdered}
              pressed={active?.orderedList ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleOrderedList().run()}
            />
            <ToolbarToggle
              label="Quote"
              icon={Quote}
              pressed={active?.blockquote ?? false}
              disabled={disabled}
              onToggle={() => editor.chain().focus().toggleBlockquote().run()}
            />
            <Separator orientation="vertical" className="mx-1 !h-5" />
          </>
        ) : null}

        <LinkPopover
          disabled={disabled}
          isActive={active?.link ?? false}
          currentHref={editor.getAttributes('link').href ?? ''}
          onApply={(href) =>
            editor.chain().focus().extendMarkRange('link').setLink({ href }).run()
          }
          onRemove={() => editor.chain().focus().extendMarkRange('link').unsetLink().run()}
        />

        {isFull ? (
          <>
            <ImagePopover
              disabled={disabled}
              onInsert={(src, alt) => editor.chain().focus().setImage({ src, alt }).run()}
            />
            <Separator orientation="vertical" className="mx-1 !h-5" />
            <ToolbarButton
              label="Undo"
              icon={Undo2}
              disabled={disabled || !(active?.canUndo ?? false)}
              onClick={() => editor.chain().focus().undo().run()}
            />
            <ToolbarButton
              label="Redo"
              icon={Redo2}
              disabled={disabled || !(active?.canRedo ?? false)}
              onClick={() => editor.chain().focus().redo().run()}
            />
          </>
        ) : null}
      </div>

      <EditorContent editor={editor} />
    </div>
  )
}

function ToolbarToggle({
  label,
  icon: Icon,
  pressed,
  disabled,
  onToggle,
}: {
  label: string
  icon: typeof Bold
  pressed: boolean
  disabled: boolean
  onToggle: () => void
}) {
  return (
    <Toggle
      size="sm"
      aria-label={label}
      title={label}
      pressed={pressed}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onPressedChange={onToggle}
    >
      <Icon className="size-4" />
    </Toggle>
  )
}

function ToolbarButton({
  label,
  icon: Icon,
  disabled,
  onClick,
}: {
  label: string
  icon: typeof Bold
  disabled: boolean
  onClick: () => void
}) {
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="size-8"
      aria-label={label}
      title={label}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
    >
      <Icon className="size-4" />
    </Button>
  )
}

/** Link editing without `window.prompt`: a popover with a URL field. */
function LinkPopover({
  disabled,
  isActive,
  currentHref,
  onApply,
  onRemove,
}: {
  disabled: boolean
  isActive: boolean
  currentHref: string
  onApply: (href: string) => void
  onRemove: () => void
}) {
  const [open, setOpen] = useState(false)
  const [href, setHref] = useState('')

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) setHref(currentHref)
      }}
    >
      <PopoverTrigger asChild>
        <Toggle
          size="sm"
          aria-label="Link"
          title="Link"
          pressed={isActive}
          disabled={disabled}
          onMouseDown={(e) => e.preventDefault()}
        >
          <Link2 className="size-4" />
        </Toggle>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-2">
        <Label htmlFor="rte-link-url">Link URL</Label>
        <Input
          id="rte-link-url"
          value={href}
          placeholder="https://example.com"
          onChange={(e) => setHref(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !href.trim()) return
            e.preventDefault()
            onApply(href.trim())
            setOpen(false)
          }}
        />
        <div className="flex justify-end gap-2">
          {isActive ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                onRemove()
                setOpen(false)
              }}
            >
              <Link2Off className="size-4" /> Remove
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            disabled={!href.trim()}
            onClick={() => {
              onApply(href.trim())
              setOpen(false)
            }}
          >
            Apply link
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** Image insert: paste a URL, or upload a file and use the returned URL. */
function ImagePopover({
  disabled,
  onInsert,
}: {
  disabled: boolean
  onInsert: (src: string, alt: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [src, setSrc] = useState('')
  const [alt, setAlt] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const insert = (url: string) => {
    onInsert(url, alt.trim())
    setSrc('')
    setAlt('')
    setOpen(false)
  }

  const upload = async (file: File) => {
    setUploading(true)
    try {
      const uploaded = await api.upload<UploadedFile>(ws('/files'), file)
      insert(uploaded.url)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-8"
          aria-label="Insert image"
          title="Insert image"
          disabled={disabled}
          onMouseDown={(e) => e.preventDefault()}
        >
          <ImagePlus className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-2">
        <Label htmlFor="rte-image-url">Image URL</Label>
        <Input
          id="rte-image-url"
          value={src}
          placeholder="https://example.com/diagram.png"
          onChange={(e) => setSrc(e.target.value)}
        />
        <Label htmlFor="rte-image-alt">Alt text</Label>
        <Input
          id="rte-image-alt"
          value={alt}
          placeholder="Describe the image"
          onChange={(e) => setAlt(e.target.value)}
        />
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="sr-only"
          aria-label="Image file"
          onChange={(e) => {
            const file = e.target.files?.[0]
            e.target.value = ''
            if (file) void upload(file)
          }}
        />
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Upload className="size-4" />
            )}
            Upload
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!src.trim() || uploading}
            onClick={() => insert(src.trim())}
          >
            Insert
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

export default RichTextEditor
