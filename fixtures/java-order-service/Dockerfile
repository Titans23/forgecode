FROM maven:3.9.16-eclipse-temurin-8-alpine AS dependencies

WORKDIR /app

COPY pom.xml ./
RUN --mount=type=cache,target=/root/.m2 \
    mvn -q -B -DskipTests dependency:go-offline

FROM dependencies AS public-test

COPY src ./src
RUN --mount=type=cache,target=/root/.m2 \
    mvn -q -B -DskipTests package

CMD [mvn, -q, -B, test]

FROM public-test AS evaluation

COPY tests/hidden ./tests/hidden

CMD [mvn, -q, -B, -Phidden-tests, -Dtest=OrderServiceHiddenTest, test]
