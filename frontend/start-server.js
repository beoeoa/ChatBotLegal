#!/usr/bin/env node

const fs = require('fs')
const path = require('path')

if (!process.env.PORT) {
  process.env.PORT = '3000'
}

const distDir = process.env.NEXT_DIST_DIR || '.next'
const standaloneDir = path.join(__dirname, distDir, 'standalone')
const standaloneStaticDir = path.join(standaloneDir, distDir, 'static')
const buildStaticDir = path.join(__dirname, distDir, 'static')
const standalonePublicDir = path.join(standaloneDir, 'public')
const publicDir = path.join(__dirname, 'public')

function ensureDirCopied(source, destination) {
  if (!fs.existsSync(source)) {
    return
  }

  // Rebuilding into the same dist directory can leave an existing standalone
  // asset directory. Always refresh it so the new server can serve its chunks.
  fs.mkdirSync(path.dirname(destination), { recursive: true })
  fs.cpSync(source, destination, { recursive: true, force: true })
}

ensureDirCopied(buildStaticDir, standaloneStaticDir)
ensureDirCopied(publicDir, standalonePublicDir)

require(path.join(standaloneDir, 'server.js'))
