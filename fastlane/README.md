fastlane documentation
----

# Installation

Make sure you have the latest version of the Xcode command line tools installed:

```sh
xcode-select --install
```

For _fastlane_ installation instructions, see [Installing _fastlane_](https://docs.fastlane.tools/#installing-fastlane)

# Available Actions

## iOS

### ios prep

```sh
[bundle exec] fastlane ios prep
```

Register the bundle ID via App Store Connect API (no Apple ID password needed)

### ios check_app

```sh
[bundle exec] fastlane ios check_app
```

Check whether the App Store Connect app record exists

### ios setup_internal

```sh
[bundle exec] fastlane ios setup_internal
```

Create internal testing group, add tester, assign latest build

### ios invite

```sh
[bundle exec] fastlane ios invite
```

Invite a personal Apple ID onto the team (so it can be an internal tester)

### ios testers

```sh
[bundle exec] fastlane ios testers
```

List team users + beta groups so we know tester options

### ios compliance

```sh
[bundle exec] fastlane ios compliance
```

Clear export compliance (no non-exempt encryption) on the latest build

### ios list_testers

```sh
[bundle exec] fastlane ios list_testers
```

List beta testers and their state

### ios status

```sh
[bundle exec] fastlane ios status
```

Report build processing status + internal tester groups

### ios ship

```sh
[bundle exec] fastlane ios ship
```

Cert -> profile -> build -> upload to TestFlight (run AFTER the app record exists)

----

This README.md is auto-generated and will be re-generated every time [_fastlane_](https://fastlane.tools) is run.

More information about _fastlane_ can be found on [fastlane.tools](https://fastlane.tools).

The documentation of _fastlane_ can be found on [docs.fastlane.tools](https://docs.fastlane.tools).
