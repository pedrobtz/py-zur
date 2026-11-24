! The sum of squares 1^2 + ... + n^2, called through .Fortran().
subroutine sumsq(n, s)
  implicit none
  integer, intent(in) :: n
  double precision, intent(out) :: s
  integer :: i
  s = 0d0
  do i = 1, n
    s = s + dble(i)**2
  end do
end subroutine sumsq
