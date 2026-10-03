import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { Navigate, useNavigate, useSearchParams } from "react-router";
import { z } from "zod";

import { keys } from "../../api/keys";
import { useMe } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { Field, Input } from "../../components/ui/Field";
import { ApiError, api, unwrap } from "../../lib/api";
import { safeNext } from "./auth";

const schema = z.object({
  email: z.string().trim().min(1, "Enter your email."),
  password: z.string().min(1, "Enter your password."),
});
type Values = z.infer<typeof schema>;

function signInError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "RATE_LIMITED") return error.detail; // "Too many attempts. Try again at 14:32."
    if (error.status === 401 || error.status === 400) return "Email or password is incorrect.";
  }
  return "Couldn't reach Baton. Check your connection and try again.";
}

export function LoginPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const me = useMe();

  // In demo mode the server lists accounts; in any other mode this is a 404 and the list is hidden.
  const demo = useQuery({
    queryKey: ["demo-users"],
    queryFn: () => unwrap(api.GET("/api/v1/demo/users")),
    retry: false,
    staleTime: Infinity,
  });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  const signIn = useMutation({
    mutationFn: (values: Values) =>
      unwrap(
        api.POST("/api/v1/auth/login", {
          body: { email: values.email, password: values.password },
        }),
      ),
    onSuccess: (data) => {
      qc.clear();
      qc.setQueryData(keys.me, data);
      void navigate(next, { replace: true });
    },
  });

  // Already signed in: don't show the form.
  if (me.isSuccess) return <Navigate to={next} replace />;

  return (
    <main className="flex min-h-full items-center justify-center bg-desk p-4">
      <div className="w-full max-w-sm rounded-panel border border-rule bg-sheet p-6">
        <h1 className="text-title">Baton</h1>
        <p className="mt-1 text-body text-pencil">Coordinate operational work across teams.</p>

        <form
          className="mt-6 flex flex-col gap-4"
          noValidate
          onSubmit={(event) => {
            void form.handleSubmit((values) => {
              signIn.mutate(values);
            })(event);
          }}
        >
          <Field label="Email" htmlFor="email" error={form.formState.errors.email?.message}>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              // eslint-disable-next-line jsx-a11y/no-autofocus -- the one field this page is for
              autoFocus
              aria-invalid={form.formState.errors.email ? true : undefined}
              {...form.register("email")}
            />
          </Field>
          <Field
            label="Password"
            htmlFor="password"
            error={form.formState.errors.password?.message}
          >
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              aria-invalid={form.formState.errors.password ? true : undefined}
              {...form.register("password")}
            />
          </Field>
          {signIn.isError && (
            <p role="alert" className="text-meta text-p0">
              {signInError(signIn.error)}
            </p>
          )}
          <Button type="submit" variant="primary" pending={signIn.isPending}>
            Sign in
          </Button>
        </form>

        {demo.data && (
          <div className="mt-6 border-t border-rule pt-4">
            <h2 className="text-section">Demo accounts</h2>
            <ul className="mt-2 flex flex-col">
              {demo.data.users.map((user) => (
                <li key={user.email}>
                  <button
                    type="button"
                    disabled={signIn.isPending}
                    onClick={() => {
                      signIn.mutate({ email: user.email, password: demo.data.password });
                    }}
                    className="flex w-full cursor-pointer items-baseline justify-between gap-3 rounded-chip px-2 py-1.5 text-left text-body hover:bg-dispatch-wash disabled:opacity-50"
                  >
                    <span>Sign in as {user.name}</span>
                    <span className="text-meta text-pencil">{describeAccount(user)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </main>
  );
}

function describeAccount(user: {
  is_admin: boolean;
  memberships: { team_key: string; role: string }[];
}): string {
  if (user.is_admin) return "Admin";
  if (user.memberships.length === 0) return "No team";
  return user.memberships.map((m) => `${m.team_key} ${m.role}`).join(", ");
}
