import type { ReactNode } from "react";

import { Link } from "react-router-dom";

import { ForbiddenState } from "../components/states";
import type { ProductUser } from "../product-api";

export function RequireAdmin({
  user,
  children,
}: {
  user: ProductUser;
  children: ReactNode;
}) {
  if (user.role !== "admin") {
    return (
      <ForbiddenState
        action={
          <Link className="secondary-button" to="/">
            返回问答工作台
          </Link>
        }
      />
    );
  }
  return <>{children}</>;
}
