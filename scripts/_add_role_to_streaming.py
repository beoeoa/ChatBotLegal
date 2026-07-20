from pathlib import Path

# Step 1: Add role prop to StreamingResponse interface and destructuring
p = Path("frontend/src/components/search/StreamingResponse.tsx")
text = p.read_text(encoding="utf-8")

# Add role to interface
old_interface = """interface StreamingResponseProps {
  isStreaming: boolean
  strategy: StrategyData | null
  answers: string[]
  finalAnswer: string | null
  ragTrace?: RagTrace | null
  procedureDetail?: ProcedureDetail | null
  recommendedForms?: ProcedureForm[] | null
  citations?: AskResponse['citations']
  groundingStatus?: string | null
}"""

new_interface = """interface StreamingResponseProps {
  isStreaming: boolean
  strategy: StrategyData | null
  answers: string[]
  finalAnswer: string | null
  ragTrace?: RagTrace | null
  procedureDetail?: ProcedureDetail | null
  recommendedForms?: ProcedureForm[] | null
  citations?: AskResponse['citations']
  groundingStatus?: string | null
  role?: 'citizen' | 'officer' | 'admin'
}"""

if old_interface in text:
    text = text.replace(old_interface, new_interface, 1)
    print("OK: Added role to interface")
else:
    print("ERROR: interface not found")
    raise SystemExit(1)

# Add role to destructuring
old_destruct = """: StreamingResponseProps) {
  const [strategyOpen, setStrategyOpen] = useState(false)"""

new_destruct = """: StreamingResponseProps) {
  const { role = 'citizen' } = props
  const [strategyOpen, setStrategyOpen] = useState(false)"""

# Actually need to destructure from the function params
old_func_start = """: StreamingResponseProps) {
  const [strategyOpen, setStrategyOpen] = useState(false)"""

# Better approach: add role to destructured params
old_params = """: StreamingResponseProps) {
  const [strategyOpen, setStrategyOpen] = useState(false)"""

new_params = """: StreamingResponseProps) {
  const { role = 'citizen' } = {}
  const [strategyOpen, setStrategyOpen] = useState(false)"""

# Let me check the actual destructuring pattern
func_line = text.find("export function StreamingResponse({")
if func_line >= 0:
    # Find the closing ) of destructuring
    close_paren = text.find("): StreamingResponseProps)", func_line)
    if close_paren >= 0:
        before_close = text[func_line:close_paren]
        # Check if role is already destructured
        if "role" not in before_close.split(":")[0]:
            # Add role to destructuring
            # Find last comma before ):
            last_comma = before_close.rfind(",")
            if last_comma > 0:
                new_before = before_close[:last_comma+1] + "\n  role," + before_close[last_comma+1:]
                text = text[:func_line] + new_before + text[close_paren:]
                print("OK: Added role to destructuring")
            else:
                print("ERROR: could not find comma in destructuring")
                raise SystemExit(1)
        else:
            print("SKIP: role already in destructuring")
    else:
        print("ERROR: could not find closing paren")
        raise SystemExit(1)
else:
    print("ERROR: function not found")
    raise SystemExit(1)

p.write_text(text, encoding="utf-8", newline="\n")
print("StreamingResponse.tsx updated")
