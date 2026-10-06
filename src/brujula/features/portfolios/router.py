"""Endpoints del portafolio (HU-02). Las modificaciones exigen CSRF."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from brujula.features.auth.dependencies import CsrfProtectedUser, CurrentUser
from brujula.features.portfolios.schemas import HoldingCreate, HoldingOut, HoldingUpdate
from brujula.features.portfolios.service import (
    HoldingNotFoundError,
    PlanLimitError,
    PortfolioService,
)

router = APIRouter(prefix="/portfolio", tags=["portafolio"])


def get_portfolio_service(request: Request) -> PortfolioService:
    service: PortfolioService = request.app.state.portfolio_service
    return service


Service = Annotated[PortfolioService, Depends(get_portfolio_service)]
NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="Posición inexistente")


@router.get("/holdings")
async def list_holdings(user: CurrentUser, service: Service) -> list[HoldingOut]:
    return await service.list_holdings(user)


@router.post("/holdings", status_code=status.HTTP_201_CREATED)
async def add_holding(user: CsrfProtectedUser, service: Service, data: HoldingCreate) -> HoldingOut:
    """Valida el ticker contra el universo e indica la cobertura (completa o solo precio)."""
    try:
        return await service.add(user, data)
    except PlanLimitError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from None


@router.patch("/holdings/{holding_id}")
async def update_holding(
    user: CsrfProtectedUser, service: Service, holding_id: UUID, data: HoldingUpdate
) -> HoldingOut:
    try:
        return await service.update(user, holding_id, data)
    except HoldingNotFoundError:
        raise NOT_FOUND from None


@router.delete("/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holding(user: CsrfProtectedUser, service: Service, holding_id: UUID) -> Response:
    try:
        await service.delete(user, holding_id)
    except HoldingNotFoundError:
        raise NOT_FOUND from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
