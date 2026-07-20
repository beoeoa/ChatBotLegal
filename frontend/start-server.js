#!/usr/bin/env node

const fs = require('fs')
const path = require('path')

if (!process.env.PORT) {
  process.env.PORT = '8502'
}

const standaloneDir = path.join(__dirname, '.next', 'standalone')
const standaloneStaticDir = path.join(standaloneDir, '.next', 'static')
const buildStaticDir = path.join(__dirname, '.next', 'static')
const standalonePublicDir = path.join(standaloneDir, 'public')
const publicDir = path.join(__dirname, 'public')

function ensureDirCopied(source, destination) {
  if (!fs.existsSync(source)) {
    return
  }

  if (!fs.existsSync(destination)) {
    fs.mkdirSync(path.dirname(destination), { recursive: true })
    fs.cpSync(source, destination, { recursive: true })
  }
}

ensureDirCopied(buildStaticDir, standaloneStaticDir)
ensureDirCopied(publicDir, standalonePublicDir)

require('./.next/standalone/server.js')
