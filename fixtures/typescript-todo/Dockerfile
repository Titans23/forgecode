FROM node:22.23-bookworm-slim AS dependencies

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

FROM dependencies AS public-test

COPY tsconfig.json ./
COPY src ./src
COPY tests/public ./tests/public
RUN npm run build

CMD [npm, test]

FROM public-test AS evaluation

COPY tests/hidden ./tests/hidden

CMD [npm, run, test:hidden]
