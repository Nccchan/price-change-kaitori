#!/bin/bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# .envファイルを作成（GAS Webhook URL等の設定）
cat > "$CLAUDE_PROJECT_DIR/.env" << 'EOF'
GAS_WEBHOOK_URL=https://script.google.com/macros/s/AKfycbxppdSa5_-jnLBTkXZBRGpXaNx27Fb80UkqbktkZCJyICW6HvUxsYHRqK2o6vIT5_NH_A/exec
SPREADSHEET_ID=1PBMNNYHliomlgeNsvZgiccrfOWpIJbYPb9EMFtSAgdw
EOF

# Pythonパッケージをインストール
pip install -q -r "$CLAUDE_PROJECT_DIR/requirements.txt"
