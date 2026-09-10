const { spawnSync } = require('child_process')
const path = require('path')

function fail(message) {
  process.stderr.write(`${message}\n`)
  process.exit(1)
}

let commandArguments
try {
  commandArguments = JSON.parse(process.env.TCB_CLI_ARGUMENTS || '[]')
} catch (error) {
  fail(`Invalid TCB_CLI_ARGUMENTS: ${error.message}`)
}

if (!Array.isArray(commandArguments) || commandArguments.some((item) => typeof item !== 'string')) {
  fail('TCB_CLI_ARGUMENTS must be a JSON array of strings')
}

const npxCli = path.join(path.dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npx-cli.js')
const result = spawnSync(
  process.execPath,
  [npxCli, '--yes', '--package', '@cloudbase/cli', 'tcb', ...commandArguments],
  { encoding: 'utf8', windowsHide: true }
)

if (result.stdout) process.stdout.write(result.stdout)
if (result.stderr) process.stderr.write(result.stderr)
process.exit(Number.isInteger(result.status) ? result.status : 1)
