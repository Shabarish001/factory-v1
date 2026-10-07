# Preferred stack defaults

These defaults guide new application design. They reduce repeated technology choices and let Factory build reusable implementation and testing workflows around a known stack.

## Frontend

- React with Next.js
- TypeScript
- Tailwind CSS
- shadcn/ui and Radix primitives where they improve accessibility or reduce custom component work

## Backend and data

- Firebase as the default backend platform
- Firestore for application data
- Firebase Authentication for identity
- Firebase Storage for user and application files
- Cloud Functions or framework server-side functions when trusted server execution is required

The architecture must still define Firestore security rules, data ownership, indexes, migrations, and local emulator coverage. Client code must not receive privileged Firebase credentials.

## Deployment

- Vercel for the web frontend
- Firebase services for backend, authentication, data, storage, and Firebase-hosted functions

Deployment remains an explicit, approved operation. Selecting these defaults does not authorize Factory to create projects, link accounts, or deploy.

## Testing

- Vitest for unit and component-level tests
- Playwright for browser and end-to-end verification
- Firebase Emulator Suite for tests that exercise Firebase services when that coverage is needed

Use deterministic linting, type checks, builds, and project-native tests alongside these tools.

## When alternatives are justified

Factory may recommend another technology when a substantial technical reason exists. Examples include an existing product stack, a required platform Firebase or Vercel cannot support, regulatory or data-residency constraints, measured cost or scale limits, offline-first requirements, or a dependency that conflicts with the default stack.

Preference alone is not enough. The proposal must compare the default and alternative against the requirement, explain migration and operating costs, and record the decision in an architecture decision record.

For an existing target repository, preserve its established stack unless the requested work explicitly includes a migration and the migration has been approved.
